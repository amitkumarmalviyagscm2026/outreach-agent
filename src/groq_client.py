"""Groq REST client for structured JSON generation -- the backup LLM.

Groq is a separate service from Google, with its own free tier, so it can
keep a run going while Gemini's free tier is returning 503s. Used only via
src/llm.py, which tries Gemini first.

Confirmed from Groq's docs (console.groq.com/docs):
  - Endpoint: POST https://api.groq.com/openai/v1/chat/completions
    (OpenAI-compatible), header "Authorization: Bearer <key>"
  - Structured output: response_format = {"type": "json_schema",
    "json_schema": {"name", "strict": true, "schema"}}. Strict mode is
    supported on openai/gpt-oss-120b and openai/gpt-oss-20b, and requires
    every property listed in "required" and additionalProperties: false
    on every object.
  - Free tier (per model): 30 RPM, 1K RPD, 8K tokens/minute, 200K
    tokens/day. Tokens-per-minute is the binding limit for this pipeline.
  - 429 responses carry a "retry-after" header (seconds).
  - Key from https://console.groq.com/keys -- free, no credit card.

Also exposes chat_with_browser_search() -- Groq's built-in real-time web
search tool on the openai/gpt-oss-* models, used by groq_contact_search.py
as the free contact-research fallback once Crustdata's balance runs out
(replacing an earlier Gemini-Search-grounding attempt that turned out to
be structurally unworkable: Gemini 2.5, the only family with free
grounding, is closed to new Google accounts, and Gemini 3.x has no free
grounding at all -- confirmed via live 404s, not assumption). Groq's
`groq/compound*` models (its OWN earlier grounded-search offering) were
separately decommissioned 2026-09-21, but browser_search on gpt-oss is a
distinct, currently-active feature -- confirmed via Groq's own docs.
"""
from __future__ import annotations

import json
import re
import time

import httpx

URL = "https://api.groq.com/openai/v1/chat/completions"

# Both have strict json_schema support and separate free-tier quotas.
GROQ_MODELS = ["openai/gpt-oss-120b", "openai/gpt-oss-20b"]

RETRIES_PER_MODEL = 3
BACKOFF_BASE_SECONDS = 5.0
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}

# The free tier allows 8K tokens/minute, and a single request asking for
# more than that can be rejected outright. Callers may request more
# (discovery asks for 16K to fit 100 companies), so cap it here. A
# 100-company discovery list is ~6K tokens, so it still fits -- but it's
# the one call that could run close to the limit on Groq.
MAX_COMPLETION_TOKENS_CAP = 7000

# A drafting call is roughly 1-1.5K tokens (prompt + output + a little
# reasoning); at 8K tokens/minute that's ~6 calls a minute. 10s between
# successful calls keeps under that with margin.
PACING_SECONDS_AFTER_SUCCESS = 10.0


def to_strict_json_schema(schema: dict) -> dict:
    """Converts the Gemini-style schema used across this codebase
    (uppercase OpenAPI types) into the strict JSON Schema Groq expects:
    lowercase types, additionalProperties: false on every object, and
    every property required."""
    out: dict = {}
    for key, value in schema.items():
        if key == "type" and isinstance(value, str):
            out["type"] = value.lower()
        elif key == "properties":
            out["properties"] = {k: to_strict_json_schema(v) for k, v in value.items()}
        elif key == "items":
            out["items"] = to_strict_json_schema(value)
        else:
            out[key] = value
    if out.get("type") == "object":
        out["additionalProperties"] = False
        out["required"] = list(out.get("properties", {}).keys())
    return out


def _retry_after(resp: httpx.Response) -> float | None:
    value = resp.headers.get("retry-after")
    try:
        return float(value) if value is not None else None
    except ValueError:
        return None


def _call_one_model(model: str, body: dict, api_key: str) -> tuple[dict | None, Exception | None]:
    """Returns the FULL response dict (not just parsed content), since
    generate_json() needs choices[0].message.content parsed as JSON while
    chat_with_browser_search() also needs choices[0].message.executed_tools,
    which isn't reachable from content alone."""
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    last_error: Exception | None = None

    with httpx.Client(timeout=90.0) as client:
        for attempt in range(RETRIES_PER_MODEL):
            try:
                resp = client.post(URL, json={**body, "model": model}, headers=headers)

                if resp.status_code in RETRYABLE_STATUS_CODES:
                    server_delay = _retry_after(resp)
                    wait = (server_delay + 1.0) if server_delay is not None else BACKOFF_BASE_SECONDS * (2 ** attempt)
                    print(
                        f"  Groq [{model}] {resp.status_code} ({resp.reason_phrase}), "
                        f"backing off {wait:.0f}s (attempt {attempt + 1}/{RETRIES_PER_MODEL})"
                    )
                    last_error = httpx.HTTPStatusError(
                        f"{resp.status_code} {resp.reason_phrase}", request=resp.request, response=resp
                    )
                    time.sleep(wait)
                    continue

                resp.raise_for_status()
                data = resp.json()
                # Touch the path now so a malformed body is caught here
                # (and retried) rather than surfacing later in a caller
                # that assumed success.
                _ = data["choices"][0]["message"]
                return data, None

            except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
                last_error = exc
                wait = BACKOFF_BASE_SECONDS * (2 ** attempt)
                print(
                    f"  Groq [{model}] unusable response ({type(exc).__name__}), "
                    f"retrying in {wait:.0f}s (attempt {attempt + 1}/{RETRIES_PER_MODEL})"
                )
                time.sleep(wait)
            except httpx.HTTPStatusError as exc:
                # 400 (bad request/schema) or 401/403 (key) -- not transient.
                detail = exc.response.text[:300] if exc.response is not None else ""
                return None, RuntimeError(f"Groq [{model}] call failed (non-retryable): {exc} {detail}")
            except httpx.HTTPError as exc:
                last_error = exc
                wait = BACKOFF_BASE_SECONDS * (2 ** attempt)
                print(f"  Groq [{model}] network error ({type(exc).__name__}), retrying in {wait:.0f}s")
                time.sleep(wait)

    return None, last_error


def generate_json(
    prompt: str,
    schema: dict,
    api_key: str,
    system_instruction: str | None = None,
    max_output_tokens: int = 4096,
) -> dict:
    """Same contract as gemini_client.generate_json: returns the parsed
    JSON object matching `schema`, or raises RuntimeError."""
    messages = []
    if system_instruction:
        messages.append({"role": "system", "content": system_instruction})
    messages.append({"role": "user", "content": prompt})

    body = {
        "messages": messages,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "response",
                "strict": True,
                "schema": to_strict_json_schema(schema),
            },
        },
        "max_completion_tokens": min(max_output_tokens, MAX_COMPLETION_TOKENS_CAP),
        "temperature": 0.7,
        # gpt-oss models reason before answering; reasoning tokens count
        # against the 8K tokens/minute limit, and this task doesn't need
        # deep reasoning.
        "reasoning_effort": "low",
    }

    last_error: Exception | None = None
    for model in GROQ_MODELS:
        data, error = _call_one_model(model, body, api_key)
        if data is not None:
            time.sleep(PACING_SECONDS_AFTER_SUCCESS)
            content = data["choices"][0]["message"]["content"]
            return json.loads(content)
        last_error = error
        print(f"  Groq model '{model}' failed ({error}), trying next Groq model if any remain")

    raise RuntimeError(f"Groq call failed on all models {GROQ_MODELS}: {last_error}")


_URL_PATTERN = re.compile(r'https?://[^\s"\'<>\\)]+')


def _extract_urls(executed_tools) -> list[str]:
    """Pulls every URL out of the executed_tools field, whatever its exact
    nesting -- Groq's docs describe executed_tools as carrying the browser
    search's raw queries/results but don't pin down an exact schema, so
    this reads it as an opaque blob and regex-extracts URLs rather than
    assuming a specific key path that might not match reality (the same
    "don't trust the docs' shape, verify against real output" lesson this
    project has hit repeatedly)."""
    if not executed_tools:
        return []
    blob = json.dumps(executed_tools)
    seen: dict[str, None] = {}
    for match in _URL_PATTERN.finditer(blob):
        seen[match.group(0)] = None
    return list(seen)


def chat_with_browser_search(
    prompt: str,
    api_key: str,
    system_instruction: str | None = None,
    models: list[str] = GROQ_MODELS,
    max_output_tokens: int = 2048,
) -> tuple[str, list[str]]:
    """Calls Groq's built-in `browser_search` tool (real-time web search,
    supported on the openai/gpt-oss-* models) -- lets the model search the
    live web rather than answering from training data. Returns
    (text, cited_urls), where cited_urls comes from the response's
    executed_tools field (see _extract_urls).

    Can't combine tool_choice="required" for browser_search with strict
    response_format json_schema mode -- documented as unreliable on
    gpt-oss-120b (response_format silently ignored). `text` is therefore
    free-form; parse it tolerantly (see contact_verification.py) rather
    than relying on schema enforcement, same approach already used for
    Gemini's grounding in gemini_client.py.
    """
    messages = []
    if system_instruction:
        messages.append({"role": "system", "content": system_instruction})
    messages.append({"role": "user", "content": prompt})

    body = {
        "messages": messages,
        "tools": [{"type": "browser_search"}],
        "tool_choice": "required",
        "max_completion_tokens": min(max_output_tokens, MAX_COMPLETION_TOKENS_CAP),
        "temperature": 0.3,
    }

    last_error: Exception | None = None
    for model in models:
        data, error = _call_one_model(model, body, api_key)
        if data is not None:
            time.sleep(PACING_SECONDS_AFTER_SUCCESS)
            message = data["choices"][0]["message"]
            text = message.get("content") or ""
            sources = _extract_urls(message.get("executed_tools"))
            return text, sources
        last_error = error
        print(f"  Groq model '{model}' failed ({error}), trying next Groq model if any remain")

    raise RuntimeError(f"Groq browser_search call failed on all models {models}: {last_error}")
