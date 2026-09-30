"""Contact research via Gemini's Google Search grounding -- the free
fallback once Crustdata's credit balance is exhausted.

Replaces an earlier attempt built against the standalone Custom Search
JSON API, which turned out to be closed to new Google Cloud accounts
entirely (confirmed via real error responses, not assumption -- see git
log). Grounding is a different Gemini API feature: 500 free grounded
search requests/day, but ONLY on Gemini 2.5 Flash / 2.5 Flash-Lite --
confirmed at ai.google.dev/gemini-api/docs/pricing on 2026-09-30, after a
live test run showed every grounded call 429-ing regardless of backoff.
Gemini 3.x models (what the rest of this pipeline uses) get NO free-tier
grounding at all -- it's paid-only there, which is what the 429s actually
were. This module must be called with a 2.5-family model list (see
config.py's GEMINI_GROUNDING_MODELS), on the SAME GEMINI_API_KEY this
pipeline already uses for discovery.py. No new signup, no new secret.

Groq was also considered as an alternative when the Gemini 429s first
showed up (Groq is a separate service/quota from Gemini). Ruled out: Groq's
only web-search-capable models (`groq/compound`, `groq/compound-beta`)
were decommissioned on 2026-09-21 -- confirmed live via Groq's own docs.
Groq's remaining models (openai/gpt-oss-*, llama-3.3-*, used in llm.py for
discovery.py's backup) have no browsing/search tool on the free tier at
all, so they cannot look up a real, verifiable contact -- only Gemini's
2.5-family grounding can.

Design, and why:
  - Can't reuse gemini_client.generate_json()'s strict response_schema
    mode -- Gemini rejects combining the "google_search" tool with
    response_mime_type="application/json" except on Pro-tier models,
    which this free-tier pipeline doesn't use. The model is instead
    instructed (not enforced) to return a single JSON object, parsed
    tolerantly (_parse_json_loosely: strips markdown code fences, finds
    the first {...} block).
  - The model's own claimed linkedin_url is NOT trusted on its word --
    LLMs confabulate plausible-looking URLs. It's cross-checked against
    the actual grounding citations Google returned
    (groundingMetadata.groundingChunks[].web.uri); if the claimed URL
    doesn't match any real citation, the whole result is discarded as
    unverified (blank Contact), same "blank beats wrong" rule used
    everywhere else in this pipeline. If verification behaves oddly in
    the first live run (e.g. citation URIs turn out to be Google redirect
    links rather than direct linkedin.com URLs, which the docs don't
    rule out), the raw sources are printed so it's debuggable from the
    job log rather than a silent zero-results wall.
"""
from __future__ import annotations

import json
import re

from src.crustdata_client import Contact, _contains_word
from src.gemini_client import generate_grounded_text

_JSON_OBJECT_PATTERN = re.compile(r"\{.*\}", re.DOTALL)
_CODE_FENCE_PATTERN = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE | re.MULTILINE)

SYSTEM_PROMPT = """You are a precise research assistant with live web search access via your \
search tool. Your ONLY job: find ONE real, currently-employed person at a specific company \
in India, in a specific job function, and report their name, title, and LinkedIn profile URL.

STRICT RULES:
1. You MUST use your search tool to find this person. Never answer from memory or guess --
if your search does not turn up a confident, current match, say so.
2. Only report a person if your search actually surfaced a real LinkedIn profile URL for
them. Never invent, guess, or construct a plausible-looking URL -- only report a URL you
genuinely found via search.
3. If you cannot find a confident, current match, respond with {"found": false}. A blank
answer is far better than a wrong or outdated one.
4. Respond with ONLY a single JSON object -- no markdown code fences, no explanation before
or after it. Exactly one of these two shapes:
{"found": true, "name": "...", "title": "...", "linkedin_url": "https://www.linkedin.com/in/..."}
{"found": false}
"""


def _build_prompt(company_name: str, title_keywords: list[str]) -> str:
    keyword_clause = ", ".join(title_keywords[:5])
    return (
        f"Find one person currently working at '{company_name}' in India, whose job title "
        f"relates to one of: {keyword_clause}. If more than one person matches, prefer the "
        f"more senior one (Head, Director, VP, Chief) over a junior one (Associate, Intern, "
        f"Executive). Search for their LinkedIn profile and report it."
    )


def _parse_json_loosely(text: str) -> dict | None:
    stripped = _CODE_FENCE_PATTERN.sub("", text.strip()).strip()
    match = _JSON_OBJECT_PATTERN.search(stripped)
    candidate = match.group(0) if match else stripped
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        return None


def _normalize_url(url: str) -> str:
    url = url.lower().strip().rstrip("/")
    return re.sub(r"^https?://(www\.)?", "", url)


def _url_is_verified(claimed_url: str, sources: list[dict]) -> bool:
    """Cross-checks the model's claimed URL against what it actually
    cited. Returns False (unverified) if there's nothing to compare
    against, or nothing matches -- never assumes good faith."""
    if not claimed_url or not sources:
        return False
    claimed = _normalize_url(claimed_url)
    return any(_normalize_url(s.get("uri", "")) == claimed for s in sources)


def find_contact(
    role: str,
    company_name: str,
    title_keywords: list[str],
    gemini_api_key: str,
    gemini_models: list[str],
    exclude: list[str] | None = None,
) -> Contact:
    """One grounded search per role. Returns a Contact tagged
    source="gemini_search" -- lower confidence than a Crustdata match,
    same as the (now-removed) Google Custom Search fallback was, and
    flagged the same way in the output for a human to verify."""
    exclude = exclude or []
    prompt = _build_prompt(company_name, title_keywords)

    try:
        text, sources = generate_grounded_text(
            gemini_models, prompt, gemini_api_key, system_instruction=SYSTEM_PROMPT
        )
    except RuntimeError as exc:
        print(f"  {role} (Gemini Search fallback) failed: {exc}")
        return Contact(role=role, source="gemini_search")

    parsed = _parse_json_loosely(text)
    if parsed is None:
        print(f"  {role} (Gemini Search fallback): unparseable response: {text[:300]!r}")
        return Contact(role=role, source="gemini_search")

    if not parsed.get("found"):
        return Contact(role=role, source="gemini_search")

    name = parsed.get("name")
    title = parsed.get("title") or ""
    claimed_url = parsed.get("linkedin_url") or ""

    if not name or not claimed_url:
        return Contact(role=role, source="gemini_search")

    if any(_contains_word(title, ex) for ex in exclude):
        print(f"  {role} (Gemini Search fallback): rejected '{name}' -- title '{title}' matches exclude list")
        return Contact(role=role, source="gemini_search")

    if not _url_is_verified(claimed_url, sources):
        print(
            f"  {role} (Gemini Search fallback): rejected '{name}' -- claimed URL not found "
            f"among cited sources {[s.get('uri') for s in sources][:5]}"
        )
        return Contact(role=role, source="gemini_search")

    print(f"  {role} (Gemini Search fallback): {name} -- {title} -- verified via citation")
    return Contact(role=role, name=name, title=title or None, linkedin_url=claimed_url, source="gemini_search")
