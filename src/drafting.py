"""Per-contact message drafting via Gemini: connection note + follow-up.

Hard rule: only reference facts present in the Contact/Company objects
passed in. If a field (e.g. title) is missing, write around it -- never
guess. This mirrors the standing rule from interactive runs: blank beats
fabricated, and every note must read as written for that specific person,
not a template with the name swapped in.
"""
from __future__ import annotations

from dataclasses import dataclass

from src.config import CONNECTION_NOTE_MAX_CHARS, GEMINI_MODEL
from src.crustdata_client import Contact
from src.discovery import Company
from src.gemini_client import generate_json

DRAFT_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "connection_note": {
            "type": "STRING",
            "description": f"LinkedIn connection request note. Hard limit {CONNECTION_NOTE_MAX_CHARS} characters.",
        },
        "follow_up_message": {
            "type": "STRING",
            "description": "Message to send after the connection request is accepted.",
        },
        "facts_used": {
            "type": "ARRAY",
            "items": {"type": "STRING"},
            "description": "List of specific facts from the input this note actually references (e.g. 'title', 'company_name'). Used for a downstream fact-check.",
        },
    },
    "required": ["connection_note", "follow_up_message", "facts_used"],
}

SYSTEM_PROMPT = f"""You draft LinkedIn outreach messages for a business-development \
/ campus-hiring outreach program. You will be given:
- the user's own sector of interest (what THEY are reaching out on behalf of)
- a target company and its sector
- one contact at that company: their name, title (may be missing), role \
category (hr_ta or ops_scm), and whether any IIM Udaipur alumni connection \
exists at that company

STRICT RULES:
1. Reference ONLY facts explicitly given to you. If title is missing/None, \
do not invent or guess a title, seniority, or department -- write a note \
that works without it.
2. Never fabricate a fact about the company (its size, products, recent \
news, etc.) that was not given to you.
3. Connection note: {CONNECTION_NOTE_MAX_CHARS} characters MAXIMUM, hard \
limit. Reference both the user's sector and the target company's sector/ \
business briefly, and the contact's actual function (hr_ta vs ops_scm) so \
an HR/TA note and an Ops/Supply-Chain note read distinctly, not as one \
template with the name swapped in.
4. Never use a combined salutation like "Sir/Ma'am" or "Sir or Ma'am". If \
the contact's gender cannot be confidently inferred from their name, omit \
the honorific entirely rather than guessing.
5. No placeholder tokens of any kind ({{name}}, [Company], TBD, <title>, etc.) \
may appear in the output -- every field must be the final text.
6. The follow-up message is sent after the connection is accepted -- it can \
be slightly longer, but stay professional and specific, not generic.
7. Return ONLY the JSON object matching the given schema -- no markdown \
fences, no commentary outside the fields.
"""


@dataclass(frozen=True)
class DraftedMessages:
    connection_note: str
    follow_up_message: str
    facts_used: list[str]


def draft_messages(
    user_sector: str,
    company: Company,
    contact: Contact,
    api_key: str,
    alumni_note: str | None = None,
) -> DraftedMessages:
    facts = {
        "user_sector": user_sector,
        "target_company_name": company.name,
        "target_company_rationale": company.rationale,
        "contact_role_category": contact.role,
        "contact_name": contact.name,
        "contact_title": contact.title,
        "alumni_connection": alumni_note,
    }
    known_facts = {k: v for k, v in facts.items() if v}

    prompt = (
        "Draft the connection note and follow-up for this contact. "
        f"Known facts (use ONLY these, nothing else): {known_facts}"
    )

    data = generate_json(
        GEMINI_MODEL,
        prompt,
        DRAFT_SCHEMA,
        api_key,
        system_instruction=SYSTEM_PROMPT,
    )

    return DraftedMessages(
        connection_note=data["connection_note"],
        follow_up_message=data["follow_up_message"],
        facts_used=data.get("facts_used", []),
    )
