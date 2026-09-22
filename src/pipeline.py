"""Orchestrates discovery -> per-company research -> drafting -> QA -> xlsx.

A failure researching or drafting for one company must not kill a run of
up to 100 -- each company is wrapped so the loop always reaches the end
and writes whatever it has, with failures visible via QA_FLAG rather than
a crashed job and zero output.

The workbook is checkpointed to disk after EVERY company, not just once at
the end -- a real 100-company run showed sustained Gemini 503s can push
total runtime past a job timeout, and a single end-of-run write means a
kill at company 90/100 loses all 90 companies' worth of paid-for API
calls, not just the unfinished ones. The checkpoint write is cheap (a few
KB, sub-second), so paying that cost on every iteration is worth the
guarantee.
"""
from __future__ import annotations

import time

from src.config import Secrets, clamp_company_count
from src.crustdata_client import CANDIDATES_PER_SEARCH, CREDITS_PER_RESULT, Contact, CrustdataClient
from src.discovery import Company, rank_companies
from src.drafting import DraftedMessages, draft_company_messages
from src import llm
from src.llm import LLMKeys
from src.qa import validate_contact_messages
from src.workbook import OutputRow, build_output_path, write_workbook


def _draft_company(
    user_sector: str, company: Company, contacts: list[Contact], keys: LLMKeys
) -> tuple[dict[str, DraftedMessages], str]:
    """One Gemini call for all of a company's contacts. Returns
    ({role: messages}, error_flag). Never raises -- a failed call becomes
    a QA flag on the row rather than stopping the run."""
    try:
        return draft_company_messages(user_sector, company, contacts, keys), ""
    except Exception as exc:  # noqa: BLE001 -- deliberately broad: never let one company kill the run
        return {}, f"drafting failed: {exc}"


def _messages_and_flag(contact: Contact, drafted: dict[str, DraftedMessages]) -> tuple[str | None, str | None, str]:
    """Pulls one contact's messages out of the per-company result and runs
    the QA gate on them. Returns (note, followup, qa_flag)."""
    if not contact.name:
        return None, None, "no contact found"
    msgs = drafted.get(contact.role)
    if msgs is None:
        return None, None, ""  # the company-level drafting flag already explains why

    qa = validate_contact_messages(msgs.connection_note, msgs.follow_up_message, contact.linkedin_url)
    flag = f"{contact.role}: " + "; ".join(qa.flags) if not qa.ok else ""
    return msgs.connection_note, msgs.follow_up_message, flag


def run_pipeline(sector: str, requested_count: int, mode: str, secrets: Secrets) -> str:
    count = clamp_company_count(requested_count, mode)
    print(f"Mode={mode}, requested={requested_count}, using count={count}")

    output_path = build_output_path(sector, mode)
    print(f"Checkpointing to: {output_path} (saved after every company)")

    keys = LLMKeys(gemini=secrets.gemini_api_key, groq=secrets.groq_api_key)
    companies = rank_companies(sector, count, keys)
    print(f"Discovery returned {len(companies)} companies")

    projected_requests = len(companies) * 2  # 2 person/search calls per company (HR/TA, Ops/SCM)
    # worst case: every call returns its full CANDIDATES_PER_SEARCH results
    projected_credits = projected_requests * CANDIDATES_PER_SEARCH * CREDITS_PER_RESULT
    print(
        f"Projected Crustdata requests: {projected_requests} "
        f"(up to ~{projected_credits:.2f} credits, since person/search bills per result returned)"
    )

    crustdata = CrustdataClient(secrets.crustdata_api_key)
    rows: list[OutputRow] = []
    qa_flagged = 0
    start_time = time.monotonic()

    try:
        for i, company in enumerate(companies, start=1):
            print(f"[{i}/{len(companies)}] {company.name} (searching as '{company.search_name}')")
            row_flags: list[str] = []

            try:
                hr_contact, ops_contact = crustdata.get_two_contacts(company.search_name)
            except Exception as exc:  # noqa: BLE001 -- one company's failure must not stop the run
                print(f"  contact research failed: {exc}")
                hr_contact = Contact(role="hr_ta")
                ops_contact = Contact(role="ops_scm")
                row_flags.append(f"contact research failed: {exc}")

            drafted, draft_flag = _draft_company(
                sector, company, [hr_contact, ops_contact], keys
            )
            if draft_flag:
                row_flags.append(draft_flag)

            hr_note, hr_followup, hr_flag = _messages_and_flag(hr_contact, drafted)
            ops_note, ops_followup, ops_flag = _messages_and_flag(ops_contact, drafted)

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

            write_workbook(rows, output_path)

            elapsed = time.monotonic() - start_time
            avg_per_company = elapsed / i
            remaining = avg_per_company * (len(companies) - i)
            print(
                f"  checkpoint saved -- elapsed {elapsed / 60:.1f}m, "
                f"est. {remaining / 60:.1f}m remaining "
                f"({avg_per_company:.0f}s/company avg)"
            )
    finally:
        crustdata.close()

    print(crustdata.counter.summary())
    print(llm.stats.summary())
    print(f"QA summary: {qa_flagged}/{len(rows)} rows flagged for review")
    print(f"Workbook written: {output_path}")
    return output_path
