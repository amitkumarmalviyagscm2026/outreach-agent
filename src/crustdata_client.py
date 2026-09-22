"""Crustdata Person Search client: exactly 2 contacts per company (HR/TA, Ops/SCM).

Written against Crustdata's actual documented Person Search API (confirmed
directly from docs.crustdata.com, not guessed) -- this replaces an earlier
best-effort placeholder version written before the docs were reachable.

  Endpoint: POST https://api.crustdata.com/person/search
  Headers:  authorization: Bearer <key>
            content-type: application/json
            x-api-version: 2025-11-01
  Pricing:  0.03 credits PER RESULT RETURNED, not per request -- a search
            that matches nobody costs nothing. With limit=1 per call, a
            full 100-company run costs at most 100 * 2 * 0.03 = 6 credits
            for contact research.

  Confirmed response field paths (used below, not guessed):
    - Name:              basic_profile.name
    - Current title:     experience.employment_details.current[].title
    - LinkedIn URL:      social_handles.professional_network_identifier.profile_url
    - Person ID:         crustdata_person_id

There is no separate "cheap DB tier vs. live tier" for Person Search the
way the original plan assumed -- it's one endpoint, priced per result.
Person Search can filter directly on the employer's current company name
and title in a single structured query, so no separate company-lookup
step (and no company ID) is needed at all.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import httpx

BASE_URL = "https://api.crustdata.com"
API_VERSION = "2025-11-01"
PERSON_SEARCH_PATH = "/person/search"

CREDITS_PER_RESULT = 0.03

HR_TA_TITLE_KEYWORDS = [
    "Human Resources", "HR", "Talent Acquisition", "Recruiter", "Recruiting",
    "People", "Campus", "CHRO",
]
OPS_SCM_TITLE_KEYWORDS = [
    "Supply Chain", "Operations", "Plant Head", "Manufacturing", "Ops",
    "COO", "Chief Operating",
]

# Legal-entity suffixes stripped before building a company-name filter. The
# "(.)" operator requires every word in OUR filter value to appear in the
# target string -- so a generated name like "Cipla Limited" would silently
# zero-match a Crustdata record stored without "Limited"/"Ltd". Stripping
# these first makes the match robust to that kind of suffix drift.
_LEGAL_SUFFIXES = (
    "limited", "ltd", "ltd.", "pvt", "pvt.", "private", "inc", "inc.",
    "incorporated", "corporation", "corp", "corp.", "llc", "group",
    "enterprises", "holdings", "plc",
)

CONTACT_FIELDS = [
    "crustdata_person_id",
    "basic_profile.name",
    "basic_profile.current_title",
    "experience.employment_details.current.title",
    "experience.employment_details.current.name",
    "social_handles.professional_network_identifier.profile_url",
]

MAX_RETRIES = 3
BACKOFF_BASE_SECONDS = 2.0


def _core_company_name(name: str) -> str:
    words = name.split()
    while words and words[-1].strip(".,").lower() in _LEGAL_SUFFIXES:
        words.pop()
    return " ".join(words) if words else name


@dataclass
class Contact:
    role: str  # "hr_ta" or "ops_scm"
    name: str | None = None
    title: str | None = None
    linkedin_url: str | None = None


@dataclass
class CallCounter:
    """Tracks calls/results this run for a job-log cost estimate. Person
    Search is priced per result returned, so results_returned is a direct
    proxy for spend, not just a request tally."""
    requests_made: int = 0
    results_returned: int = 0
    companies_with_no_match: list[str] = field(default_factory=list)

    def estimated_credits(self) -> float:
        return round(self.results_returned * CREDITS_PER_RESULT, 2)

    def summary(self) -> str:
        return (
            f"Crustdata: {self.requests_made} requests, "
            f"{self.results_returned} results returned "
            f"(~{self.estimated_credits()} credits), "
            f"{len(self.companies_with_no_match)} companies with no contact "
            "found on either role"
        )


class CrustdataClient:
    def __init__(self, api_key: str):
        self._headers = {
            "authorization": f"Bearer {api_key}",
            "content-type": "application/json",
            "x-api-version": API_VERSION,
        }
        self._client = httpx.Client(base_url=BASE_URL, headers=self._headers, timeout=30.0)
        self.counter = CallCounter()

    def close(self) -> None:
        self._client.close()

    def _search_person(self, company_name: str, title_keywords: list[str]) -> dict | None:
        core_name = _core_company_name(company_name)
        body = {
            "filters": {
                "op": "and",
                "conditions": [
                    {
                        "field": "experience.employment_details.current.company_name",
                        "type": "(.)",
                        "value": core_name,
                    },
                    {
                        "op": "or",
                        "conditions": [
                            {
                                "field": "experience.employment_details.current.title",
                                "type": "(.)",
                                "value": kw,
                            }
                            for kw in title_keywords
                        ],
                    },
                ],
            },
            "fields": CONTACT_FIELDS,
            "limit": 1,
        }

        last_error: Exception | None = None
        for attempt in range(MAX_RETRIES):
            try:
                resp = self._client.post(PERSON_SEARCH_PATH, json=body)
                self.counter.requests_made += 1
                if resp.status_code == 429:
                    wait = BACKOFF_BASE_SECONDS * (2 ** attempt)
                    print(
                        f"  Crustdata 429, backing off {wait:.0f}s "
                        f"(attempt {attempt + 1}/{MAX_RETRIES})"
                    )
                    time.sleep(wait)
                    continue
                resp.raise_for_status()
                data = resp.json()
                profiles = data.get("profiles", [])
                self.counter.results_returned += len(profiles)
                return profiles[0] if profiles else None
            except httpx.HTTPError as exc:
                last_error = exc
                time.sleep(BACKOFF_BASE_SECONDS)

        raise RuntimeError(f"Crustdata person/search failed for '{company_name}': {last_error}")

    def _to_contact(self, role: str, profile: dict | None) -> Contact:
        contact = Contact(role=role)
        if profile is None:
            return contact

        contact.name = (profile.get("basic_profile") or {}).get("name")

        current_roles = (
            (profile.get("experience") or {})
            .get("employment_details", {})
            .get("current", [])
        )
        if current_roles:
            contact.title = current_roles[0].get("title")
        if not contact.title:
            contact.title = (profile.get("basic_profile") or {}).get("current_title")

        contact.linkedin_url = (
            (profile.get("social_handles") or {})
            .get("professional_network_identifier", {})
            .get("profile_url")
        )
        return contact

    def get_two_contacts(self, company_name: str) -> tuple[Contact, Contact]:
        """Returns exactly two Contact objects (HR/TA, Ops/SCM) for a
        company. Either or both may be empty (all fields None) if
        Crustdata has no match -- never invented, per the QA rule that
        blank beats wrong."""
        try:
            hr_profile = self._search_person(company_name, HR_TA_TITLE_KEYWORDS)
        except RuntimeError as exc:
            print(f"  HR/TA search failed: {exc}")
            hr_profile = None

        try:
            ops_profile = self._search_person(company_name, OPS_SCM_TITLE_KEYWORDS)
        except RuntimeError as exc:
            print(f"  Ops/SCM search failed: {exc}")
            ops_profile = None

        if hr_profile is None and ops_profile is None:
            self.counter.companies_with_no_match.append(company_name)

        return self._to_contact("hr_ta", hr_profile), self._to_contact("ops_scm", ops_profile)
