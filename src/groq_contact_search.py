"""Contact research via Groq's browser_search built-in tool -- the free
fallback once Crustdata's credit balance is exhausted.

Third attempt at a free contact-search fallback this session. History:
  1. Google Custom Search JSON API -- closed to new Google Cloud accounts
     entirely (confirmed via live 403s).
  2. Gemini's own Google Search grounding -- structurally unworkable on
     this account: Gemini 2.5 (the only family with free grounding) is
     closed to new users ("no longer available to new users", confirmed
     via a live 404 with that exact message), and Gemini 3.x (what this
     account CAN call) gets no free grounding at all, paid-only.
  3. This module: Groq's `browser_search` tool on openai/gpt-oss-120b /
     openai/gpt-oss-20b -- a different feature from Groq's own
     `groq/compound*` models (which WERE decommissioned 2026-09-21).
     browser_search is confirmed live and active via Groq's docs as of
     2026-09-30. Uses the same GROQ_API_KEY already optional in this
     pipeline for discovery.py's backup LLM -- no new secret, but note
     it's now load-bearing for the fallback too, not just a nice-to-have.

*** NOT YET EXERCISED AGAINST THE LIVE API. *** Given this session's track
record (five straight cases -- Custom Search, Apollo, Captain Data,
Enrich.so, Gemini grounding -- where a service's real behavior didn't
match its docs once tested), run a real `--count 1 --mode test` after
Crustdata's credits are exhausted (or temporarily point CRUSTDATA_API_KEY
at an exhausted/invalid key to force the fallback path) and read the log
before trusting this in a `full` run.

Design, and why (mirrors gemini_contact_search.py's now-removed approach):
  - Can't reuse groq_client.generate_json()'s strict response_format mode
    -- documented as unreliable when combined with tool_choice="required"
    on gpt-oss-120b (response_format silently ignored). The model is
    instead instructed (not enforced) to return a single JSON object,
    parsed tolerantly (contact_verification.parse_json_loosely).
  - The model's own claimed linkedin_url is NOT trusted on its word --
    cross-checked against URLs actually pulled from the response's
    executed_tools field (see groq_client._extract_urls). If the claimed
    URL doesn't match a real one the tool actually visited/cited, the
    whole result is discarded as unverified (blank Contact) -- "blank
    beats wrong", same rule used everywhere else in this pipeline.
"""
from __future__ import annotations

from src.contact_verification import parse_json_loosely, url_is_verified
from src.crustdata_client import Contact, _contains_word
from src.groq_client import chat_with_browser_search

SYSTEM_PROMPT = """You are a precise research assistant with live web search access via your \
browser_search tool. Your ONLY job: find ONE real, currently-employed person at a specific \
company in India, in a specific job function, and report their name, title, and LinkedIn \
profile URL.

STRICT RULES:
1. You MUST use your browser_search tool to find this person. Never answer from memory or
guess -- if your search does not turn up a confident, current match, say so.
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


def find_contact(
    role: str,
    company_name: str,
    title_keywords: list[str],
    groq_api_key: str,
    exclude: list[str] | None = None,
) -> Contact:
    """One browser-search-backed lookup per role. Returns a Contact tagged
    source="groq_search" -- lower confidence than a Crustdata match,
    flagged the same way in the output for a human to verify."""
    exclude = exclude or []
    prompt = _build_prompt(company_name, title_keywords)

    try:
        text, sources = chat_with_browser_search(prompt, groq_api_key, system_instruction=SYSTEM_PROMPT)
    except RuntimeError as exc:
        print(f"  {role} (Groq Search fallback) failed: {exc}")
        return Contact(role=role, source="groq_search")

    parsed = parse_json_loosely(text)
    if parsed is None:
        print(f"  {role} (Groq Search fallback): unparseable response: {text[:300]!r}")
        return Contact(role=role, source="groq_search")

    if not parsed.get("found"):
        return Contact(role=role, source="groq_search")

    name = parsed.get("name")
    title = parsed.get("title") or ""
    claimed_url = parsed.get("linkedin_url") or ""

    if not name or not claimed_url:
        return Contact(role=role, source="groq_search")

    if any(_contains_word(title, ex) for ex in exclude):
        print(f"  {role} (Groq Search fallback): rejected '{name}' -- title '{title}' matches exclude list")
        return Contact(role=role, source="groq_search")

    if not url_is_verified(claimed_url, sources):
        print(
            f"  {role} (Groq Search fallback): rejected '{name}' -- claimed URL not found "
            f"among cited sources {sources[:5]}"
        )
        return Contact(role=role, source="groq_search")

    print(f"  {role} (Groq Search fallback): {name} -- {title} -- verified via citation")
    return Contact(role=role, name=name, title=title or None, linkedin_url=claimed_url, source="groq_search")
