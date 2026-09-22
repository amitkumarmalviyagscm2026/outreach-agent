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

GEMINI_MODEL in config.py is an alias ("gemini-flash-latest"), not a pinned
version -- see the README's Gemini-404 troubleshooting section if that
alias ever stops resolving.

A first real run also surfaced 503 "Service Unavailable" from Gemini's
free tier under load -- retried here with real exponential backoff (the
original version used a flat delay, which wasn't enough to let a transient
503 clear). 429 (rate limit) and 500/502/503/504 (server-side, transient)
are retried; anything else (e.g. 400 for a malformed request) fails fast
since retrying it would just waste attempts on a non-transient error.
"""
from __future__ import annotations

import json
import time

import httpx

BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
MAX_RETRIES = 6
BACKOFF_BASE_SECONDS = 4.0
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}

# A real run showed only the first ~2 back-to-back calls succeeding before
# every subsequent one hit 429/503 and stayed there through all 6 retries --
# a free-tier per-minute rate limit, not transient overload. Pacing every
# successful call by this much keeps us under that limit proactively
# instead of only reacting to it after the fact.
PACING_SECONDS_AFTER_SUCCESS = 6.0


def generate_json(
    model: str,
    prompt: str,
    schema: dict,
    api_key: str,
    system_instruction: str | None = None,
    max_output_tokens: int = 4096,
) -> dict:
    """Calls Gemini with the response forced into `schema`. Retries with
    exponential backoff on rate-limit (429) and transient server errors
    (500/502/503/504) -- both are expected fairly often on the free tier
    under load, not signs of a broken request."""
    url = f"{BASE_URL}/models/{model}:generateContent?key={api_key}"

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
    with httpx.Client(timeout=60.0) as client:
        for attempt in range(MAX_RETRIES):
            try:
                resp = client.post(url, json=body)

                if resp.status_code in RETRYABLE_STATUS_CODES:
                    wait = BACKOFF_BASE_SECONDS * (2 ** attempt)
                    print(
                        f"  Gemini {resp.status_code} ({resp.reason_phrase}), "
                        f"backing off {wait:.0f}s (attempt {attempt + 1}/{MAX_RETRIES})"
                    )
                    last_error = httpx.HTTPStatusError(
                        f"{resp.status_code} {resp.reason_phrase}", request=resp.request, response=resp
                    )
                    time.sleep(wait)
                    continue

                resp.raise_for_status()
                data = resp.json()
                text = data["candidates"][0]["content"]["parts"][0]["text"]
                result = json.loads(text)
                time.sleep(PACING_SECONDS_AFTER_SUCCESS)
                return result

            except (KeyError, IndexError, json.JSONDecodeError) as exc:
                # Malformed/unexpected response shape -- still worth a
                # retry (transient truncation, etc.) but not a network error.
                last_error = exc
                wait = BACKOFF_BASE_SECONDS * (2 ** attempt)
                time.sleep(wait)
            except httpx.HTTPStatusError as exc:
                # A non-retryable status (e.g. 400 bad request, 401/403
                # auth) -- fail fast rather than burn through attempts.
                raise RuntimeError(f"Gemini call failed (non-retryable): {exc}") from exc
            except httpx.HTTPError as exc:
                # Network-level failure (timeout, connection reset) -- retry.
                last_error = exc
                wait = BACKOFF_BASE_SECONDS * (2 ** attempt)
                time.sleep(wait)

    raise RuntimeError(f"Gemini call failed after {MAX_RETRIES} attempts: {last_error}")
