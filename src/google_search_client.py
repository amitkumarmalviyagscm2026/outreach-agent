"""Google Custom Search JSON API client -- the free fallback contact source
when Crustdata's credit balance runs out.

Confirmed from Google's official API reference (developers.google.com/custom-search):
  - Endpoint: GET https://customsearch.googleapis.com/customsearch/v1
  - Required params: key (API key), cx (Programmable Search Engine ID), q
  - Free tier: 100 queries/day, no credit card, no ongoing cost
  - Response: {"items": [{"title", "link", "snippet", ...}, ...]}
  - siteSearch=linkedin.com + siteSearchFilter=i restricts results to
    LinkedIn without needing "site:" in the query text

This is a fundamentally different, lower-confidence data source than
Crustdata: instead of querying a structured database with an exact
"current employer" filter, it searches Google's index of public LinkedIn
profile pages and returns whatever titles/snippets Google has indexed.
There is no reliable structured field for "current title at this specific
company" -- only a text snippet the profile owner or Google wrote, which
can be stale, incomplete, or about a past role. Every contact sourced this
way is tagged source="google_search" so the pipeline can flag it for a
human to verify, rather than presenting it with the same confidence as a
Crustdata match.

Rate limit: Google returns 429 (or sometimes 403) once the 100/day quota
is used up. Treated as a hard stop for the rest of the run -- the quota
is shared across the whole day, not per-run, so retrying wastes calls
that could go to the next company instead.
"""
from __future__ import annotations

import re
import time

import httpx

from src.crustdata_client import Contact, seniority_score, _contains_word

BASE_URL = "https://customsearch.googleapis.com/customsearch/v1"
MAX_RETRIES = 2
BACKOFF_BASE_SECONDS = 3.0

QUOTA_EXCEEDED_STATUS_CODES = {429, 403}


class GoogleSearchQuotaExceeded(RuntimeError):
    """The day's 100 free queries are used up. Caller should stop trying
    Google Search for the rest of this run -- it won't recover today."""


# Matches a LinkedIn profile URL's path, e.g. /in/priya-sharma-123abc
_PROFILE_URL_PATTERN = re.compile(r"linkedin\.com/in/[^/?#]+", re.IGNORECASE)

# LinkedIn's indexed page titles are typically "Name - Title - Company |
# LinkedIn" or "Name | LinkedIn". Extract just the name (before the first
# " - " or " | "), since the rest is often stale or truncated by Google.
_NAME_FROM_TITLE_PATTERN = re.compile(r"^([^|\-]+?)\s*[|\-]")


def _extract_name(title: str) -> str | None:
    match = _NAME_FROM_TITLE_PATTERN.match(title)
    name = (match.group(1) if match else title).strip()
    # Reject obviously-not-a-name results (e.g. a company or jobs page
    # that slipped through the /in/ URL filter, or a name over 5 words).
    if not name or len(name.split()) > 5 or any(ch.isdigit() for ch in name):
        return None
    return name


class GoogleSearchClient:
    def __init__(self, api_key: str, cx: str):
        self._api_key = api_key
        self._cx = cx
        self._client = httpx.Client(timeout=20.0)
        self.queries_made = 0
        self.results_returned = 0

    def close(self) -> None:
        self._client.close()

    def _search(self, query: str) -> list[dict]:
        params = {
            "key": self._api_key,
            "cx": self._cx,
            "q": query,
            "siteSearch": "linkedin.com",
            "siteSearchFilter": "i",
            "num": 10,
        }

        last_error: Exception | None = None
        for attempt in range(MAX_RETRIES):
            try:
                resp = self._client.get(BASE_URL, params=params)
                self.queries_made += 1
                if resp.status_code in QUOTA_EXCEEDED_STATUS_CODES:
                    raise GoogleSearchQuotaExceeded(
                        f"Google Custom Search quota/access error (status {resp.status_code}): "
                        f"{resp.text[:500]}"
                    )
                if resp.status_code == 400:
                    # A 400 is a malformed request (bad key, bad cx, bad
                    # param) -- retrying the identical request wastes
                    # queries against the 100/day quota for no benefit.
                    # Fail fast with Google's actual error body so this is
                    # diagnosable instead of a bare "Bad Request".
                    raise RuntimeError(
                        f"Google Custom Search 400 Bad Request for query '{query}': {resp.text[:500]}"
                    )
                resp.raise_for_status()
                items = resp.json().get("items", []) or []
                self.results_returned += len(items)
                return items
            except (GoogleSearchQuotaExceeded, RuntimeError):
                raise
            except httpx.HTTPError as exc:
                last_error = exc
                if isinstance(exc, httpx.HTTPStatusError) and exc.response is not None:
                    last_error = RuntimeError(f"{exc} -- body: {exc.response.text[:500]}")
                time.sleep(BACKOFF_BASE_SECONDS * (2 ** attempt))

        raise RuntimeError(f"Google Custom Search failed for query '{query}': {last_error}")

    def find_contact(
        self, role: str, company_name: str, title_keywords: list[str], exclude: list[str] | None = None
    ) -> Contact:
        """One query per role: site:linkedin.com/in profiles matching the
        company name and a few title keywords, India. Picks the most
        senior-sounding match by the same seniority_score() used for
        Crustdata results, applied to the search snippet text. `exclude`
        mirrors Crustdata's OPS_SCM_EXCLUDE -- e.g. rejects a snippet
        mentioning "Sales Operations" when searching for Ops/SCM."""
        keyword_clause = " OR ".join(title_keywords[:4])
        query = f'"{company_name}" ({keyword_clause}) India'

        items = self._search(query)
        exclude = exclude or []

        best: tuple[int, str, str] | None = None  # (score, name, url)
        for item in items:
            link = item.get("link", "")
            if not _PROFILE_URL_PATTERN.search(link):
                continue  # not a /in/ profile page (e.g. a company or jobs page)
            name = _extract_name(item.get("title", ""))
            if not name:
                continue
            snippet = item.get("snippet", "")
            if any(_contains_word(snippet, ex) for ex in exclude):
                continue
            score = seniority_score(snippet)
            if best is None or score > best[0]:
                best = (score, name, link)

        if best is None:
            return Contact(role=role, source="google_search")

        _, name, url = best
        contact = Contact(role=role, name=name, title=None, linkedin_url=url, source="google_search")
        print(f"  {role} (Google Search fallback): {name} -- {url}")
        return contact
