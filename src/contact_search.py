"""Contact research: Crustdata first, Google Custom Search as a free
fallback once Crustdata's credit balance is exhausted.

Crustdata stays the default because it's structured, filtered, and cheap
(~30 credits for a full 100-company run) -- Google Custom Search is a
lower-confidence, free backstop, not a replacement. See
google_search_client.py for why its results need a human's eye before
trusting them the way a Crustdata match is trusted.

Once Crustdata signals an exhausted balance (crustdata_client.CrustdataExhausted),
it's skipped for the REST OF THE RUN -- credits don't come back mid-run,
so retrying it on every subsequent company would be pure waste. Every
contact found after that point is tagged source="google_search" so it's
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
from src.google_search_client import GoogleSearchClient, GoogleSearchQuotaExceeded


class ContactSearchService:
    def __init__(self, crustdata_api_key: str, google_api_key: str | None, google_cx: str | None):
        self._crustdata = CrustdataClient(crustdata_api_key)
        self._google: GoogleSearchClient | None = None
        if google_api_key and google_cx:
            self._google = GoogleSearchClient(google_api_key, google_cx)

        self._crustdata_exhausted = False
        self._google_quota_exceeded = False

    def close(self) -> None:
        self._crustdata.close()
        if self._google:
            self._google.close()

    @property
    def crustdata_counter(self):
        return self._crustdata.counter

    def _google_find(
        self, role: str, company_name: str, keywords: list[str], exclude: list[str] | None = None
    ) -> Contact:
        if self._google is None or self._google_quota_exceeded:
            return Contact(role=role)
        try:
            return self._google.find_contact(role, company_name, keywords, exclude)
        except GoogleSearchQuotaExceeded as exc:
            print(f"  {exc}\n  Google Search quota used up for today -- no fallback for the rest of this run")
            self._google_quota_exceeded = True
            return Contact(role=role)
        except RuntimeError as exc:
            print(f"  Google Search fallback failed for {role}: {exc}")
            return Contact(role=role)

    def get_two_contacts(self, company_name: str) -> tuple[Contact, Contact]:
        if self._crustdata_exhausted:
            hr = self._google_find("hr_ta", company_name, HR_TA_TITLE_KEYWORDS)
            ops = self._google_find("ops_scm", company_name, OPS_SCM_TITLE_KEYWORDS, OPS_SCM_EXCLUDE)
            return hr, ops

        try:
            return self._crustdata.get_two_contacts(company_name)
        except CrustdataExhausted as exc:
            self._crustdata_exhausted = True
            if self._google is not None:
                print(
                    f"  {exc}\n  Switching to the free Google Search fallback for the "
                    "rest of this run (lower confidence -- verify these contacts)."
                )
            else:
                print(
                    f"  {exc}\n  No GOOGLE_SEARCH_API_KEY configured, so there's no fallback -- "
                    "remaining companies will have no contacts found."
                )
            # This company's own search still gets a chance via the fallback,
            # rather than leaving it blank just because it was first in line.
            hr = self._google_find("hr_ta", company_name, HR_TA_TITLE_KEYWORDS)
            ops = self._google_find("ops_scm", company_name, OPS_SCM_TITLE_KEYWORDS, OPS_SCM_EXCLUDE)
            return hr, ops

    def summary(self) -> str:
        parts = [self._crustdata.counter.summary()]
        if self._google is not None:
            parts.append(
                f"Google Search fallback: {self._google.queries_made} queries, "
                f"{self._google.results_returned} results "
                f"(used: {self._crustdata_exhausted})"
            )
        return " | ".join(parts)
