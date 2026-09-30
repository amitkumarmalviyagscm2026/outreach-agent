"""Shared Google Gemini REST client for structured JSON generation.

Used by discovery.py (sector -> company list) -- the only LLM call left in
the pipeline; drafting.py builds messages from fixed templates instead
(see templates.py). Calls the REST API directly with httpx rather than
depending on a wrapper SDK, so there's one fewer package version to keep
in sync.

Chosen over the Anthropic API specifically because it has a genuinely free
tier: a key from https://aistudio.google.com/apikey needs no credit card,
just a Google account (rate/daily-quota limited, not a trial that runs out).

Confirmed from Google's public docs (ai.google.dev/gemini-api):
  - Endpoint: POST https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key=<key>
  - Structured output: generationConfig.response_mime_type="application/json"
    plus generationConfig.response_schema, an OpenAPI-style schema whose
    "type" values are uppercase (STRING, OBJECT, ARRAY, NUMBER, INTEGER, BOOLEAN)
  - Response: candidates[0].content.parts[0].text holds the JSON string

Real runs showed BOTH "gemini-flash-latest" and "gemini-flash-lite-latest"
hitting sustained 429/503 -- checked against this project's actual Gemini
API Usage dashboard (AI Studio), which showed 429 "Too Many Requests" as
the single largest error category. That confirms a genuine per-minute rate
limit on a brand-new free-tier project, not random cross-model instability.
generate_json() therefore does two things beyond a naive retry loop:

  1. Reads Google's own recommended wait time from a 429 response body
     (google.rpc.RetryInfo.retryDelay, e.g. "13s") and obeys THAT instead
     of a guessed exponential backoff -- a blind guess can under-wait and
     make the next retry count as yet another request against the same
     tight window, digging the hole deeper instead of clearing it.
  2. Tries each model in GEMINI_MODELS (config.py) in turn, since a
     per-project quota can still leave headroom on a model that hasn't
     been hit as hard yet.

Also exposes generate_grounded_text() -- Gemini with Google Search
grounding enabled, used by gemini_contact_search.py as the free contact-
research fallback once Crustdata's balance runs out. See that function's
docstring for why it can't reuse generate_json()'s strict-schema mode.
"""
from __future__ import annotations

import json
import re
import time

import httpx

BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
# GEMINI_MODELS (config.py) is now a single model with confirmed daily
# quota headroom (see config.py's comment) -- its failures are genuine
# transient overload, not quota exhaustion, so it's worth more patience
# per model now that there's no second model to fall through to.
RETRIES_PER_MODEL = 5
BACKOFF_BASE_SECONDS = 6.0
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}

# Real usage data (AI Studio's Gemini API Usage dashboard) showed 429 "Too
# Many Requests" as the dominant error even at a 6s/10-per-minute pace --
# that left zero margin for retries, which themselves count against the
# same limit. 12s (~5/minute) leaves real headroom.
PACING_SECONDS_AFTER_SUCCESS = 12.0

_RETRY_DELAY_PATTERN = re.compile(r"^(\d+(?:\.\d+)?)s$")


def _parse_retry_delay(resp: httpx.Response) -> float | None:
    """Reads Google's own suggested wait time from a 429/503 error body,
    if present: error.details[] contains a RetryInfo entry with a
    retryDelay like "13s". Returns None if the body isn't JSON or doesn't
    carry this field -- callers fall back to exponential backoff."""
    try:
        data = resp.json()
    except json.JSONDecodeError:
        return None

    details = (data.get("error") or {}).get("details") or []
    for detail in details:
        delay = detail.get("retryDelay")
        if isinstance(delay, str):
            match = _RETRY_DELAY_PATTERN.match(delay)
            if match:
                return float(match.group(1))
    return None


def _call_one_model(
    model: str,
    body: dict,
    api_key: str,
    retries: int,
) -> tuple[dict | None, Exception | None]:
    """Tries a single model, up to `retries` times. Returns
    (raw_response_json, None) on success or (None, last_error) on
    exhaustion -- never raises, so the caller can cleanly fall through to
    the next model in the list. Returns the FULL response dict (not just
    the text), since callers differ in what they need: generate_json()
    parses candidates[0].content.parts[0].text as JSON, while
    generate_grounded_text() also needs candidates[0].groundingMetadata,
    which isn't reachable from text alone."""
    url = f"{BASE_URL}/models/{model}:generateContent?key={api_key}"
    last_error: Exception | None = None

    with httpx.Client(timeout=60.0) as client:
        for attempt in range(retries):
            try:
                resp = client.post(url, json=body)

                if resp.status_code in RETRYABLE_STATUS_CODES:
                    server_delay = _parse_retry_delay(resp)
                    guessed_delay = BACKOFF_BASE_SECONDS * (2 ** attempt)
                    # Obey Google's own number when it gives one -- it
                    # reflects the actual remaining quota window, which a
                    # guess can't know. Add a 2s buffer since the window
                    # boundary itself is approximate.
                    wait = (server_delay + 2.0) if server_delay is not None else guessed_delay
                    source = "server-suggested" if server_delay is not None else "guessed"
                    print(
                        f"  Gemini [{model}] {resp.status_code} ({resp.reason_phrase}), "
                        f"backing off {wait:.0f}s [{source}] (attempt {attempt + 1}/{retries})"
                    )
                    last_error = httpx.HTTPStatusError(
                        f"{resp.status_code} {resp.reason_phrase}", request=resp.request, response=resp
                    )
                    time.sleep(wait)
                    continue

                resp.raise_for_status()
                data = resp.json()
                # Touch the path now so a malformed/empty response is
                # caught here (and retried) rather than surfacing later in
                # a caller that assumed success. Callers use the full
                # `data` dict, not just this text, so grounding metadata
                # (see generate_grounded_text) is still available.
                _ = data["candidates"][0]["content"]["parts"][0]["text"]
                return data, None

            except (KeyError, IndexError, json.JSONDecodeError) as exc:
                # 200 OK but unusable body -- truncated JSON (output token
                # cap hit), or no candidate text (e.g. a safety block).
                # These used to retry silently, leaving gaps in the log.
                last_error = exc
                wait = BACKOFF_BASE_SECONDS * (2 ** attempt)
                print(
                    f"  Gemini [{model}] unusable response ({type(exc).__name__}: {exc}), "
                    f"retrying in {wait:.0f}s (attempt {attempt + 1}/{retries})"
                )
                time.sleep(wait)
            except httpx.HTTPStatusError as exc:
                # A non-retryable status (e.g. 400 bad request, 401/403
                # auth) -- no point trying this model again, or another
                # model either, since it's a request/auth problem, not a
                # capacity one. Surface it immediately.
                return None, RuntimeError(f"Gemini [{model}] call failed (non-retryable): {exc}")
            except httpx.HTTPError as exc:
                last_error = exc
                wait = BACKOFF_BASE_SECONDS * (2 ** attempt)
                print(
                    f"  Gemini [{model}] network error ({type(exc).__name__}), "
                    f"retrying in {wait:.0f}s (attempt {attempt + 1}/{retries})"
                )
                time.sleep(wait)

    return None, last_error


def generate_json(
    models: list[str],
    prompt: str,
    schema: dict,
    api_key: str,
    system_instruction: str | None = None,
    max_output_tokens: int = 4096,
    retries: int = RETRIES_PER_MODEL,
) -> dict:
    """Calls Gemini with the response forced into `schema`, trying each
    model in `models` in order until one succeeds. Paces successful calls
    to stay under the free tier's per-minute limit."""
    body: dict = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "response_mime_type": "application/json",
            "response_schema": schema,
            "maxOutputTokens": max_output_tokens,
        },
    }
    if system_instruction:
        body["systemInstruction"] = {"parts": [{"text": system_instruction}]}

    last_error: Exception | None = None
    for model in models:
        data, error = _call_one_model(model, body, api_key, retries)
        if data is not None:
            time.sleep(PACING_SECONDS_AFTER_SUCCESS)
            text = data["candidates"][0]["content"]["parts"][0]["text"]
            return json.loads(text)
        last_error = error
        if isinstance(error, RuntimeError):
            # Non-retryable (bad request/auth) -- no point trying the next
            # model either, it would fail the same way.
            raise error
        print(f"  Model '{model}' exhausted its retries, trying next model if any remain")

    raise RuntimeError(f"Gemini call failed on all models {models}: {last_error}")


def generate_grounded_text(
    models: list[str],
    prompt: str,
    api_key: str,
    system_instruction: str | None = None,
    max_output_tokens: int = 2048,
    retries: int = RETRIES_PER_MODEL,
) -> tuple[str, list[dict]]:
    """Calls Gemini with Google Search grounding enabled (the "tools":
    [{"google_search": {}}] request field) -- lets the model search the
    live web and cite what it found, rather than answering from its own
    training data. Returns (text, grounding_sources), where
    grounding_sources is the list of {"title", "uri"} the model actually
    cited (from candidates[0].groundingMetadata.groundingChunks[].web).

    Cannot be combined with response_mime_type="application/json" +
    response_schema (generate_json's strict mode) -- Gemini rejects that
    combination except on the Pro-tier models, which this free-tier
    pipeline doesn't use. `text` is therefore free-form; a caller that
    wants JSON out of it must parse it tolerantly (see
    gemini_contact_search.py) rather than relying on schema enforcement.

    Free tier: 5,000 grounded search requests/month for the Gemini 3.x
    family (confirmed at ai.google.dev/gemini-api/docs/pricing), shared
    across the whole project -- the same GEMINI_API_KEY already used for
    discovery.py, no separate signup or secret needed.
    """
    body: dict = {
        "contents": [{"parts": [{"text": prompt}]}],
        "tools": [{"google_search": {}}],
        "generationConfig": {"maxOutputTokens": max_output_tokens},
    }
    if system_instruction:
        body["systemInstruction"] = {"parts": [{"text": system_instruction}]}

    last_error: Exception | None = None
    for model in models:
        data, error = _call_one_model(model, body, api_key, retries)
        if data is not None:
            time.sleep(PACING_SECONDS_AFTER_SUCCESS)
            candidate = data["candidates"][0]
            text = candidate["content"]["parts"][0]["text"]
            chunks = (candidate.get("groundingMetadata") or {}).get("groundingChunks") or []
            sources = [c["web"] for c in chunks if "web" in c and c["web"].get("uri")]
            return text, sources
        last_error = error
        if isinstance(error, RuntimeError):
            raise error
        print(f"  Model '{model}' exhausted its retries, trying next model if any remain")

    raise RuntimeError(f"Gemini grounded call failed on all models {models}: {last_error}")
