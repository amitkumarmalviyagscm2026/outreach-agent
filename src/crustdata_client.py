"""Crustdata REST client: company resolution + exactly 2 contacts/company.

*** READ THIS BEFORE FIRST RUN ***
Crustdata's docs (https://docs.crustdata.com) are gated behind a login,
so the exact endpoint paths, params and response shapes below could only
be partially confirmed from public pages. What IS confirmed:
  - Auth: header "Authorization: Bearer <key>" plus "x-api-version: 2025-11-01"
  - A company search endpoint exists at POST https://api.crustdata.com/company/search
  - A people-search path fragment "screener/person/search" exists

Everything else marked "# TODO confirm" below is a best-effort placeholder.
Log into https://app.crustdata.com/api/docs with your account, open the
Company Search and People Search (screener) pages, and fix:
  - the exact request body shape for company/search and person/search
  - which response field holds the company's internal id
  - the exact field names for a person's name / title / linkedin_url
  - whether "screener/person/search" is the cheap/DB tier and what the
    live-enrichment endpoint's path is (used only as a fallback below)
before trusting a real (non-test) run against your Crustdata balance.
Run one `--count 1 --mode test` smoke test and print the raw JSON first.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import httpx

BASE_URL = "https://api.crustdata.com"
API_VERSION = "2025-11-01"  # TODO confirm still current

COMPANY_SEARCH_PATH = "/company/search"  # confirmed to exist publicly
PERSON_SEARCH_DB_PATH = "/screener/person/search"  # TODO confirm this is the cheap/DB tier
PERSON_ENRICH_LIVE_PATH = "/person/enrich"  # TODO confirm -- placeholder path for live fallback

HR_TA_TITLE_KEYWORDS = [
    "Human Resources", "HR", "Talent Acquisition", "Recruiter", "Recruiting",
    "People", "Campus", "CHRO",
]
OPS_SCM_TITLE_KEYWORDS = [
    "Supply Chain", "Operations", "Plant Head", "Manufacturing", "Ops",
    "COO", "Chief Operating",
]

MAX_RETRIES = 3
BACKOFF_BASE_SECONDS = 2.0


@dataclass
class Contact:
    role: str  # "hr_ta" or "ops_scm"
    name: str | None = None
    title: str | None = None
    linkedin_url: str | None = None
    source_tier: str | None = None  # "db" or "live", for cost auditing


@dataclass
class CallCounter:
    """Tracks calls made this run so the job log can show projected /
    actual cost before and during the loop, without ever touching the
    Crustdata credit balance API (which may itself cost a call)."""
    db_calls: int = 0
    live_calls: int = 0
    companies_resolved: int = 0
    companies_unresolved: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"Crustdata calls used: {self.db_calls} db-tier, "
            f"{self.live_calls} live-tier "
            f"({self.companies_resolved} companies resolved, "
            f"{len(self.companies_unresolved)} unresolved)"
        )


class CrustdataClient:
    def __init__(self, api_key: str):
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "x-api-version": API_VERSION,
            "Content-Type": "application/json",
        }
        self._client = httpx.Client(base_url=BASE_URL, headers=self._headers, timeout=30.0)
        self.counter = CallCounter()

    def close(self) -> None:
        self._client.close()

    def _post(self, path: str, json_body: dict) -> dict:
        last_error: Exception | None = None
        for attempt in range(MAX_RETRIES):
            try:
                resp = self._client.post(path, json=json_body)
                if resp.status_code == 429:
                    wait = BACKOFF_BASE_SECONDS * (2 ** attempt)
                    print(f"  Crustdata 429, backing off {wait:.0f}s (attempt {attempt + 1}/{MAX_RETRIES})")
                    time.sleep(wait)
                    continue
                resp.raise_for_status()
                return resp.json()
            except httpx.HTTPStatusError as exc:
                last_error = exc
                break
            except httpx.HTTPError as exc:
                last_error = exc
                time.sleep(BACKOFF_BASE_SECONDS * (2 ** attempt))
        raise RuntimeError(f"Crustdata call to {path} failed: {last_error}")

    def resolve_company(self, company_name: str) -> dict | None:
        """Cheap company lookup. Returns None (never raises) on a miss so
        the per-company loop in pipeline.py can record it and continue."""
        try:
            data = self._post(COMPANY_SEARCH_PATH, {"query": company_name, "limit": 1})
            self.counter.db_calls += 1
        except RuntimeError as exc:
            print(f"  company lookup failed for '{company_name}': {exc}")
            self.counter.companies_unresolved.append(company_name)
            return None

        results = data.get("results") or data.get("companies") or []  # TODO confirm key
        if not results:
            self.counter.companies_unresolved.append(company_name)
            return None
        self.counter.companies_resolved += 1
        return results[0]

    def _search_person_db(self, company_id: str, title_keywords: list[str]) -> dict | None:
        body = {
            "company_id": company_id,  # TODO confirm param name
            "title_keywords": title_keywords,  # TODO confirm param name/shape
            "limit": 1,
        }
        data = self._post(PERSON_SEARCH_DB_PATH, body)
        self.counter.db_calls += 1
        results = data.get("results") or data.get("people") or []  # TODO confirm key
        return results[0] if results else None

    def _search_person_live(self, company_id: str, title_keywords: list[str]) -> dict | None:
        """Live-tier fallback -- only called when the DB tier returns
        nothing, matching the cost-conscious pattern used in interactive
        runs (DB first, live only on a genuine miss)."""
        body = {
            "company_id": company_id,
            "title_keywords": title_keywords,
            "limit": 1,
        }
        data = self._post(PERSON_ENRICH_LIVE_PATH, body)
        self.counter.live_calls += 1
        results = data.get("results") or data.get("people") or []
        return results[0] if results else None

    def _find_contact(self, role: str, company_id: str, title_keywords: list[str]) -> Contact:
        contact = Contact(role=role)
        try:
            hit = self._search_person_db(company_id, title_keywords)
            tier = "db"
            if hit is None:
                hit = self._search_person_live(company_id, title_keywords)
                tier = "live"
        except RuntimeError as exc:
            print(f"  person search failed (role={role}): {exc}")
            return contact

        if hit is None:
            return contact

        contact.name = hit.get("name")  # TODO confirm field name
        contact.title = hit.get("title") or hit.get("headline")  # TODO confirm field name
        contact.linkedin_url = hit.get("linkedin_url") or hit.get("linkedin_profile_url")  # TODO confirm
        contact.source_tier = tier
        return contact

    def get_two_contacts(self, company_name: str) -> tuple[Contact, Contact]:
        """Returns exactly two Contact objects (HR/TA, Ops/SCM) for a
        company. Either or both may be empty (all fields None) if
        Crustdata has no match -- never invented, per the QA rule that
        blank beats wrong."""
        company = self.resolve_company(company_name)
        if company is None:
            return Contact(role="hr_ta"), Contact(role="ops_scm")

        company_id = company.get("id") or company.get("company_id")  # TODO confirm field name
        hr_contact = self._find_contact("hr_ta", company_id, HR_TA_TITLE_KEYWORDS)
        ops_contact = self._find_contact("ops_scm", company_id, OPS_SCM_TITLE_KEYWORDS)
        return hr_contact, ops_contact
