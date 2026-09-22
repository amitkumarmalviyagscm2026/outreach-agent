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

GEMINI_MODEL in config.py may need updating if Google renames/retires the
free-tier Flash model by the time this runs -- check
https://ai.google.dev/gemini-api/docs/models for the current name first.
"""
from __future__ import annotations

import json
import time

import httpx

BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
MAX_RETRIES = 5
BACKOFF_BASE_SECONDS = 3.0


def generate_json(
    model: str,
    prompt: str,
    schema: dict,
    api_key: str,
    system_instruction: str | None = None,
    max_output_tokens: int = 4096,
) -> dict:
    """Calls Gemini with the response forced into `schema`. Retries with
    backoff on 429 -- expected to fire fairly often, since the free tier's
    per-minute rate limit is the main thing a 100-company run will bump
    into (not a spend cap, since there isn't one to hit)."""
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
                if resp.status_code == 429:
                    wait = BACKOFF_BASE_SECONDS * (2 ** attempt)
                    print(
                        f"  Gemini 429 (free-tier rate limit), backing off "
                        f"{wait:.0f}s (attempt {attempt + 1}/{MAX_RETRIES})"
                    )
                    time.sleep(wait)
                    continue
                resp.raise_for_status()
                data = resp.json()
                text = data["candidates"][0]["content"]["parts"][0]["text"]
                return json.loads(text)
            except (httpx.HTTPError, KeyError, IndexError, json.JSONDecodeError) as exc:
                last_error = exc
                time.sleep(BACKOFF_BASE_SECONDS)

    raise RuntimeError(f"Gemini call failed after {MAX_RETRIES} attempts: {last_error}")
