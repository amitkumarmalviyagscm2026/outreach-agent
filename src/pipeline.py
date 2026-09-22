"""Orchestrates discovery -> per-company research -> drafting -> QA -> xlsx.

A failure researching or drafting for one company must not kill a run of
up to 100 -- each company is wrapped so the loop always reaches the end
and writes whatever it has, with failures visible via QA_FLAG rather than
a crashed job and zero output.
"""
from __future__ import annotations

from src.config import Secrets, clamp_company_count
from src.crustdata_client import Contact, CrustdataClient
from src.discovery import Company, rank_companies
from src.drafting import draft_messages
from src.qa import validate_contact_messages
from src.workbook import OutputRow, write_workbook


def _draft_or_flag(user_sector: str, company: Company, contact: Contact, api_key: str) -> tuple[str | None, str | None, str]:
    """Drafts messages for one contact, returns (note, followup, qa_flag).
    Returns empty messages with a flag rather than raising, so one bad
    Gemini call doesn't stop the whole run."""
    if not contact.name:
        return None, None, "no contact found"

    try:
        drafted = draft_messages(user_sector, company, contact, api_key)
    except Exception as exc:  # noqa: BLE001 -- deliberately broad: never let one contact kill the run
        return None, None, f"drafting failed: {exc}"

    qa = validate_contact_messages(drafted.connection_note, drafted.follow_up_message, contact.linkedin_url)
    flag = "; ".join(qa.flags) if not qa.ok else ""
    return drafted.connection_note, drafted.follow_up_message, flag


def run_pipeline(sector: str, requested_count: int, mode: str, secrets: Secrets) -> str:
    count = clamp_company_count(requested_count, mode)
    print(f"Mode={mode}, requested={requested_count}, using count={count}")

    companies = rank_companies(sector, count, secrets.gemini_api_key)
    print(f"Discovery returned {len(companies)} companies")

    projected_requests = len(companies) * 2  # 2 person/search calls per company (HR/TA, Ops/SCM)
    projected_credits = projected_requests * 1 * 0.03  # worst case: every call returns its 1 allowed result
    print(
        f"Projected Crustdata requests: {projected_requests} "
        f"(up to ~{projected_credits:.2f} credits, since person/search bills per result returned)"
    )

    crustdata = CrustdataClient(secrets.crustdata_api_key)
    rows: list[OutputRow] = []
    qa_flagged = 0

    try:
        for i, company in enumerate(companies, start=1):
            print(f"[{i}/{len(companies)}] {company.name}")
            row_flags: list[str] = []

            try:
                hr_contact, ops_contact = crustdata.get_two_contacts(company.name)
            except Exception as exc:  # noqa: BLE001 -- one company's failure must not stop the run
                print(f"  contact research failed: {exc}")
                hr_contact = Contact(role="hr_ta")
                ops_contact = Contact(role="ops_scm")
                row_flags.append(f"contact research failed: {exc}")

            hr_note, hr_followup, hr_flag = _draft_or_flag(sector, company, hr_contact, secrets.gemini_api_key)
            ops_note, ops_followup, ops_flag = _draft_or_flag(sector, company, ops_contact, secrets.gemini_api_key)

            for f in (hr_flag, ops_flag):
                if f:
                    row_flags.append(f)

            if row_flags:
                qa_flagged += 1

            rows.append(
                OutputRow(
                    company=company.name,
                    hr_name=hr_contact.name,
                    hr_title=hr_contact.title,
                    hr_linkedin=hr_contact.linkedin_url,
                    hr_note=hr_note,
                    hr_followup=hr_followup,
                    ops_name=ops_contact.name,
                    ops_title=ops_contact.title,
                    ops_linkedin=ops_contact.linkedin_url,
                    ops_note=ops_note,
                    ops_followup=ops_followup,
                    qa_flag="; ".join(row_flags),
                )
            )
    finally:
        crustdata.close()

    print(crustdata.counter.summary())
    print(f"QA summary: {qa_flagged}/{len(rows)} rows flagged for review")

    output_path = write_workbook(rows, sector, mode)
    print(f"Workbook written: {output_path}")
    return output_path
