"""Contact research: Crustdata first, Gemini Search grounding as a free
fallback once Crustdata's credit balance is exhausted.

Crustdata stays the default because it's structured, filtered, and cheap
(~30 credits for a full 100-company run) -- the Gemini fallback is a
lower-confidence, free backstop, not a replacement. See
gemini_contact_search.py for how it works and what it trades off.

Once Crustdata signals an exhausted balance (crustdata_client.CrustdataExhausted),
it's skipped for the REST OF THE RUN -- credits don't come back mid-run,
so retrying it on every subsequent company would be pure waste. Every
contact found after that point is tagged source="gemini_search" so it's
visible in the output, not silently presented as if it were a normal
Crustdata match.
"""
from __future__ import annotations

from src.crustdata_client import (
    HR_TA_TITLE_KEYWORDS,
    OPS_SCM_EXCLUDE,
    OPS_SCM_TITLE_KEYWORDS,
    Contact,
    CrustdataClient,
    CrustdataExhausted,
)
from src.gemini_contact_search import find_contact as gemini_find_contact


class ContactSearchService:
    def __init__(self, crustdata_api_key: str, gemini_api_key: str, gemini_models: list[str]):
        self._crustdata = CrustdataClient(crustdata_api_key)
        self._gemini_api_key = gemini_api_key
        self._gemini_models = gemini_models

        self._crustdata_exhausted = False
        self._gemini_search_calls = 0
        self._gemini_search_found = 0

    def close(self) -> None:
        self._crustdata.close()

    @property
    def crustdata_counter(self):
        return self._crustdata.counter

    def _gemini_find(self, role: str, company_name: str, keywords: list[str], exclude: list[str] | None = None) -> Contact:
        self._gemini_search_calls += 1
        contact = gemini_find_contact(
            role, company_name, keywords, self._gemini_api_key, self._gemini_models, exclude
        )
        if contact.name:
            self._gemini_search_found += 1
        return contact

    def get_two_contacts(self, company_name: str) -> tuple[Contact, Contact]:
        if self._crustdata_exhausted:
            hr = self._gemini_find("hr_ta", company_name, HR_TA_TITLE_KEYWORDS)
            ops = self._gemini_find("ops_scm", company_name, OPS_SCM_TITLE_KEYWORDS, OPS_SCM_EXCLUDE)
            return hr, ops

        try:
            return self._crustdata.get_two_contacts(company_name)
        except CrustdataExhausted as exc:
            self._crustdata_exhausted = True
            print(
                f"  {exc}\n  Switching to the free Gemini Search fallback for the "
                "rest of this run (lower confidence -- verify these contacts)."
            )
            # This company's own search still gets a chance via the fallback,
            # rather than leaving it blank just because it was first in line.
            hr = self._gemini_find("hr_ta", company_name, HR_TA_TITLE_KEYWORDS)
            ops = self._gemini_find("ops_scm", company_name, OPS_SCM_TITLE_KEYWORDS, OPS_SCM_EXCLUDE)
            return hr, ops

    def summary(self) -> str:
        parts = [self._crustdata.counter.summary()]
        if self._gemini_search_calls:
            parts.append(
                f"Gemini Search fallback: {self._gemini_search_calls} calls, "
                f"{self._gemini_search_found} contacts found"
            )
        return " | ".join(parts)
