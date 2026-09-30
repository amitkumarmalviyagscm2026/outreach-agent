"""Contact research: Crustdata first, Groq browser_search as a free
fallback once Crustdata's credit balance is exhausted.

Crustdata stays the default because it's structured, filtered, and cheap
(~30 credits for a full 100-company run) -- the Groq fallback is a
lower-confidence, free backstop, not a replacement. See
groq_contact_search.py for how it works, what it trades off, and why it
replaced an earlier Gemini-Search-grounding attempt that turned out
structurally unworkable on this account.

Once Crustdata signals an exhausted balance (crustdata_client.CrustdataExhausted),
it's skipped for the REST OF THE RUN -- credits don't come back mid-run,
so retrying it on every subsequent company would be pure waste. Every
contact found after that point is tagged source="groq_search" so it's
visible in the output, not silently presented as if it were a normal
Crustdata match.

Without GROQ_API_KEY, there's no fallback at all -- remaining companies
just get blank contacts with a "Crustdata exhausted, no fallback
configured" flag, rather than crashing or silently skipping them.
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
from src.groq_contact_search import find_contact as groq_find_contact


class ContactSearchService:
    def __init__(self, crustdata_api_key: str, groq_api_key: str | None):
        self._crustdata = CrustdataClient(crustdata_api_key)
        self._groq_api_key = groq_api_key

        self._crustdata_exhausted = False
        self._groq_search_calls = 0
        self._groq_search_found = 0

    def close(self) -> None:
        self._crustdata.close()

    @property
    def crustdata_counter(self):
        return self._crustdata.counter

    def _groq_find(self, role: str, company_name: str, keywords: list[str], exclude: list[str] | None = None) -> Contact:
        if not self._groq_api_key:
            return Contact(role=role)
        self._groq_search_calls += 1
        contact = groq_find_contact(role, company_name, keywords, self._groq_api_key, exclude)
        if contact.name:
            self._groq_search_found += 1
        return contact

    def get_two_contacts(self, company_name: str) -> tuple[Contact, Contact]:
        if self._crustdata_exhausted:
            hr = self._groq_find("hr_ta", company_name, HR_TA_TITLE_KEYWORDS)
            ops = self._groq_find("ops_scm", company_name, OPS_SCM_TITLE_KEYWORDS, OPS_SCM_EXCLUDE)
            return hr, ops

        try:
            return self._crustdata.get_two_contacts(company_name)
        except CrustdataExhausted as exc:
            self._crustdata_exhausted = True
            if self._groq_api_key:
                print(
                    f"  {exc}\n  Switching to the free Groq Search fallback for the "
                    "rest of this run (lower confidence -- verify these contacts)."
                )
            else:
                print(f"  {exc}\n  No GROQ_API_KEY configured -- remaining companies will have blank contacts.")
            # This company's own search still gets a chance via the fallback,
            # rather than leaving it blank just because it was first in line.
            hr = self._groq_find("hr_ta", company_name, HR_TA_TITLE_KEYWORDS)
            ops = self._groq_find("ops_scm", company_name, OPS_SCM_TITLE_KEYWORDS, OPS_SCM_EXCLUDE)
            return hr, ops

    def summary(self) -> str:
        parts = [self._crustdata.counter.summary()]
        if self._groq_search_calls:
            parts.append(
                f"Groq Search fallback: {self._groq_search_calls} calls, "
                f"{self._groq_search_found} contacts found"
            )
        return " | ".join(parts)
