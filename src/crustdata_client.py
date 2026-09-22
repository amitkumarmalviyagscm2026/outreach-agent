"""Crustdata Person Search client: exactly 2 contacts per company (HR/TA, Ops/SCM).

Written against Crustdata's documented Person Search API (confirmed from
docs.crustdata.com, not guessed).

  Endpoint: POST https://api.crustdata.com/person/search
  Headers:  authorization: Bearer <key>
            content-type: application/json
            x-api-version: 2025-11-01
  Pricing:  0.03 credits PER RESULT RETURNED, not per request -- a search
            that matches nobody costs nothing.

  Confirmed response field paths (used below, not guessed):
    - Name:              basic_profile.name
    - Current roles:     experience.employment_details.current[] -> {name, title}
    - LinkedIn URL:      social_handles.professional_network_identifier.profile_url
    - Person ID:         crustdata_person_id

Each search filters on current employer name + a title keyword + location
India, fetches CANDIDATES_PER_SEARCH people, and keeps the most senior one.
Two problems in real output drove this design:

  - Wrong company. "ITC Limited" returned "Brian Slocum, Chief Operating
    Officer" -- almost certainly a different, non-Indian company that also
    has "ITC" in its name. The India location filter removes that class
    of mismatch.
  - Arbitrary, junior, or wrong-role picks. With limit=1 the pipeline took
    whoever Crustdata returned first ("Senior Office Associate - Corp HR"
    as ITC's HR contact). And people often hold several current roles, so
    reading the FIRST current role's title could show a role unrelated to
    the search ("Head - Digital Solutions" as Britannia's HR/TA contact).
    Now the title is read from the specific current role that matched --
    at this employer, with a role keyword -- and candidates are ranked by
    the seniority of that role.

Cost: with CANDIDATES_PER_SEARCH = 5, a 100-company run costs at most
100 * 2 * 5 * 0.03 = 30 credits (vs. 6 at limit=1). Searches that match
fewer people cost proportionally less.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

import httpx

BASE_URL = "https://api.crustdata.com"
API_VERSION = "2025-11-01"
PERSON_SEARCH_PATH = "/person/search"

CREDITS_PER_RESULT = 0.03
CANDIDATES_PER_SEARCH = 5

# Crustdata's basic_profile.location.country uses full country names
# ("India", "United States"), per its docs.
TARGET_COUNTRY = "India"

# Deliberately specific. Earlier versions included bare "People" and "Ops",
# which match unrelated titles ("People's Choice ...", "Sales Ops").
HR_TA_TITLE_KEYWORDS = [
    "Human Resources", "HR", "Talent Acquisition", "Recruitment", "Recruiting",
    "Recruiter", "Campus", "CHRO", "People Operations", "HRBP",
]
OPS_SCM_TITLE_KEYWORDS = [
    "Supply Chain", "Plant Head", "Manufacturing", "Logistics", "Procurement",
    "COO", "Chief Operating", "Operations",
]
# "Operations" alone also matches "Sales Operations", "HR Operations", etc.
# A matched Ops/SCM title containing any of these is rejected.
OPS_SCM_EXCLUDE = ["sales", "marketing", "hr", "human resources", "finance", "it"]

# Seniority tiers, checked highest first; a title's score is the first tier
# with any matching word. Word-boundary matches, so "COO" doesn't match
# inside "Coordinator" and "Head" doesn't match inside "Headquarters".
_SENIORITY_TIERS: list[tuple[int, list[str]]] = [
    (5, ["chief", "chro", "coo", "cpo", "cxo"]),
    (4, ["vice president", "vp", "svp", "evp", "president"]),
    (3, ["head", "director", "general manager", "gm"]),
    (2, ["senior manager", "sr manager", "sr. manager", "agm", "dgm", "lead", "principal"]),
    (1, ["manager"]),
]
_JUNIOR_WORDS = ["intern", "trainee", "assistant", "associate", "executive", "coordinator", "student"]

# Legal-entity suffixes stripped before building a company-name filter. The
# "(.)" operator requires every word in OUR filter value to appear in the
# target string -- so "Cipla Limited" would zero-match a record stored as
# "Cipla". Discovery now supplies a short search_name, but this stays as a
# safety net in case a legal suffix slips through.
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


def _contains_word(text: str, phrase: str) -> bool:
    return re.search(r"(?<![a-z0-9])" + re.escape(phrase.lower()) + r"(?![a-z0-9])", text.lower()) is not None


def seniority_score(title: str | None) -> int:
    """Higher is more senior. Junior markers ("Associate", "Executive",
    "Intern") knock a title down unless it also carries a senior marker
    ("Associate Director" stays a Director)."""
    if not title:
        return -1
    # "Assistant/Deputy General Manager" contains "General Manager" but is
    # a rung below it -- checked first so the tier-3 match doesn't win.
    if any(_contains_word(title, w) for w in ("assistant general manager", "deputy general manager")):
        return 2
    for score, words in _SENIORITY_TIERS:
        if any(_contains_word(title, w) for w in words):
            return score
    if any(_contains_word(title, w) for w in _JUNIOR_WORDS):
        return -1
    return 0


def _title_matches_role(title: str, keywords: list[str], exclude: list[str]) -> bool:
    if not any(_contains_word(title, kw) for kw in keywords):
        return False
    return not any(_contains_word(title, ex) for ex in exclude)


def _employer_matches(employer: str | None, company_name: str) -> bool:
    """Every word of the search name must appear in the employer name --
    mirrors Crustdata's own "(.)" semantics, applied again locally to pick
    WHICH of a person's current roles is the one at this company."""
    if not employer:
        return False
    return all(_contains_word(employer, w) for w in _core_company_name(company_name).split())


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


def pick_best_contact(
    role: str,
    profiles: list[dict],
    company_name: str,
    keywords: list[str],
    exclude: list[str],
) -> Contact:
    """From a list of candidate profiles, returns the most senior person
    whose CURRENT role at THIS company has a title matching the role.
    A profile is skipped if none of its current roles is both at this
    employer and role-relevant -- the search matched on some other
    combination of their roles, so it isn't a real fit."""
    best: tuple[int, dict, str] | None = None

    for profile in profiles:
        current_roles = (
            (profile.get("experience") or {})
            .get("employment_details", {})
            .get("current", [])
        ) or []

        matching_titles = [
            r.get("title")
            for r in current_roles
            if r.get("title")
            and _employer_matches(r.get("name"), company_name)
            and _title_matches_role(r["title"], keywords, exclude)
        ]
        if not matching_titles:
            continue

        title = max(matching_titles, key=seniority_score)
        score = seniority_score(title)
        if best is None or score > best[0]:
            best = (score, profile, title)

    if best is None:
        return Contact(role=role)

    _, profile, title = best
    return Contact(
        role=role,
        name=(profile.get("basic_profile") or {}).get("name"),
        title=title,
        linkedin_url=(
            (profile.get("social_handles") or {})
            .get("professional_network_identifier", {})
            .get("profile_url")
        ),
    )


def build_search_body(company_name: str, title_keywords: list[str]) -> dict:
    return {
        "filters": {
            "op": "and",
            "conditions": [
                {
                    "field": "experience.employment_details.current.company_name",
                    "type": "(.)",
                    "value": _core_company_name(company_name),
                },
                {
                    "field": "basic_profile.location.country",
                    "type": "=",
                    "value": TARGET_COUNTRY,
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
        "limit": CANDIDATES_PER_SEARCH,
    }


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

    def _search_people(self, company_name: str, title_keywords: list[str]) -> list[dict]:
        body = build_search_body(company_name, title_keywords)

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
                profiles = resp.json().get("profiles", []) or []
                self.counter.results_returned += len(profiles)
                return profiles
            except httpx.HTTPError as exc:
                last_error = exc
                time.sleep(BACKOFF_BASE_SECONDS)

        raise RuntimeError(f"Crustdata person/search failed for '{company_name}': {last_error}")

    def _find(self, role: str, company_name: str, keywords: list[str], exclude: list[str]) -> Contact:
        try:
            profiles = self._search_people(company_name, keywords)
        except RuntimeError as exc:
            print(f"  {role} search failed: {exc}")
            return Contact(role=role)
        contact = pick_best_contact(role, profiles, company_name, keywords, exclude)
        if contact.name:
            print(f"  {role}: {contact.name} -- {contact.title} (best of {len(profiles)})")
        return contact

    def get_two_contacts(self, company_name: str) -> tuple[Contact, Contact]:
        """Returns exactly two Contact objects (HR/TA, Ops/SCM) for a
        company. Either or both may be empty (all fields None) if
        Crustdata has no suitable match -- never invented, per the QA rule
        that blank beats wrong."""
        hr = self._find("hr_ta", company_name, HR_TA_TITLE_KEYWORDS, [])
        ops = self._find("ops_scm", company_name, OPS_SCM_TITLE_KEYWORDS, OPS_SCM_EXCLUDE)

        if hr.name is None and ops.name is None:
            self.counter.companies_with_no_match.append(company_name)
        return hr, ops
