"""Shared Google Gemini REST client for structured JSON generation.

Used by discovery.py (sector -> company list) and drafting.py (per-contact
messages). Calls the REST API directly with httpx rather than depending on
a wrapper SDK, so there's one fewer package version to keep in sync.

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
"""
from __future__ import annotations

import json
import re
import time

import httpx

BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
RETRIES_PER_MODEL = 3
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
) -> tuple[dict | None, Exception | None]:
    """Tries a single model, up to RETRIES_PER_MODEL times. Returns
    (result, None) on success or (None, last_error) on exhaustion --
    never raises, so the caller can cleanly fall through to the next
    model in the list."""
    url = f"{BASE_URL}/models/{model}:generateContent?key={api_key}"
    last_error: Exception | None = None

    with httpx.Client(timeout=60.0) as client:
        for attempt in range(RETRIES_PER_MODEL):
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
                        f"backing off {wait:.0f}s [{source}] (attempt {attempt + 1}/{RETRIES_PER_MODEL})"
                    )
                    last_error = httpx.HTTPStatusError(
                        f"{resp.status_code} {resp.reason_phrase}", request=resp.request, response=resp
                    )
                    time.sleep(wait)
                    continue

                resp.raise_for_status()
                data = resp.json()
                text = data["candidates"][0]["content"]["parts"][0]["text"]
                return json.loads(text), None

            except (KeyError, IndexError, json.JSONDecodeError) as exc:
                last_error = exc
                time.sleep(BACKOFF_BASE_SECONDS * (2 ** attempt))
            except httpx.HTTPStatusError as exc:
                # A non-retryable status (e.g. 400 bad request, 401/403
                # auth) -- no point trying this model again, or another
                # model either, since it's a request/auth problem, not a
                # capacity one. Surface it immediately.
                return None, RuntimeError(f"Gemini [{model}] call failed (non-retryable): {exc}")
            except httpx.HTTPError as exc:
                last_error = exc
                time.sleep(BACKOFF_BASE_SECONDS * (2 ** attempt))

    return None, last_error


def generate_json(
    models: list[str],
    prompt: str,
    schema: dict,
    api_key: str,
    system_instruction: str | None = None,
    max_output_tokens: int = 4096,
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
        result, error = _call_one_model(model, body, api_key)
        if result is not None:
            time.sleep(PACING_SECONDS_AFTER_SUCCESS)
            return result
        last_error = error
        if isinstance(error, RuntimeError):
            # Non-retryable (bad request/auth) -- no point trying the next
            # model either, it would fail the same way.
            raise error
        print(f"  Model '{model}' exhausted its retries, trying next model if any remain")

    raise RuntimeError(f"Gemini call failed on all models {models}: {last_error}")
