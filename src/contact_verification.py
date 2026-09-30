"""Shared helpers for LLM-based contact-search fallbacks (currently
groq_contact_search.py): tolerant JSON extraction from a free-text model
response, and cross-checking a model's claimed URL against a list of URLs
it actually cited -- never trusting a claimed LinkedIn URL on the model's
word alone, since LLMs confabulate plausible-looking URLs.
"""
from __future__ import annotations

import json
import re

_JSON_OBJECT_PATTERN = re.compile(r"\{.*\}", re.DOTALL)
_CODE_FENCE_PATTERN = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE | re.MULTILINE)


def parse_json_loosely(text: str) -> dict | None:
    """Strips markdown code fences and pulls out the first {...} block --
    models asked for "only JSON" still sometimes wrap it in prose or a
    code fence."""
    stripped = _CODE_FENCE_PATTERN.sub("", text.strip()).strip()
    match = _JSON_OBJECT_PATTERN.search(stripped)
    candidate = match.group(0) if match else stripped
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        return None


def normalize_url(url: str) -> str:
    url = url.lower().strip().rstrip("/")
    return re.sub(r"^https?://(www\.)?", "", url)


def url_is_verified(claimed_url: str, sources: list[str]) -> bool:
    """Returns False (unverified) if there's nothing to compare against,
    or nothing matches -- never assumes good faith."""
    if not claimed_url or not sources:
        return False
    claimed = normalize_url(claimed_url)
    return any(normalize_url(s) == claimed for s in sources)
