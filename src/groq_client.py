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
"""
from __future__ import annotations

import json
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
                content = resp.json()["choices"][0]["message"]["content"]
                return json.loads(content), None

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
        result, error = _call_one_model(model, body, api_key)
        if result is not None:
            time.sleep(PACING_SECONDS_AFTER_SUCCESS)
            return result
        last_error = error
        print(f"  Groq model '{model}' failed ({error}), trying next Groq model if any remain")

    raise RuntimeError(f"Groq call failed on all models {GROQ_MODELS}: {last_error}")
