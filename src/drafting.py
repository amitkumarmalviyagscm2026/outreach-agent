"""Per-company message drafting via Gemini: connection note + follow-up for
each contact found at that company, in ONE call.

Drafting both contacts together halves the number of Gemini calls per run
(one per company instead of one per contact). On a free tier where calls
are the scarce resource -- 500/day and frequent transient 503s -- that
doubles how many full runs fit in a day's quota and halves the exposure to
overload errors.

Hard rule: only reference facts present in the Contact/Company objects
passed in. If a field (e.g. title) is missing, write around it -- never
guess. Blank beats fabricated, and every note must read as written for
that specific person, not a template with the name swapped in.
"""
from __future__ import annotations

from dataclasses import dataclass

from src.config import CONNECTION_NOTE_MAX_CHARS, GEMINI_MODELS
from src.crustdata_client import Contact
from src.discovery import Company
from src.gemini_client import generate_json

_MESSAGE_SCHEMA = {
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
    },
    "required": ["connection_note", "follow_up_message"],
}

SYSTEM_PROMPT = f"""You draft LinkedIn outreach messages for a business-development \
/ campus-hiring outreach program. You will be given:
- the user's own sector of interest (what THEY are reaching out on behalf of)
- a target company and why it's relevant
- one or two contacts at that company, each keyed by role category: \
"hr_ta" (HR / talent acquisition) or "ops_scm" (operations / supply chain), \
with their name and title (title may be missing)

Draft a separate connection note and follow-up for EACH contact given.

STRICT RULES:
1. Reference ONLY facts explicitly given to you. If a title is missing, \
do not invent or guess a title, seniority, or department -- write a note \
that works without it.
2. Never fabricate a fact about the company (its size, products, recent \
news, etc.) that was not given to you.
3. Connection note: {CONNECTION_NOTE_MAX_CHARS} characters MAXIMUM, hard \
limit. Reference the user's sector and the target company briefly, and the \
contact's actual function, so the hr_ta note and the ops_scm note read \
distinctly -- not one template with the name swapped in.
4. Never use a combined salutation like "Sir/Ma'am" or "Sir or Ma'am". If \
the contact's gender cannot be confidently inferred from their name, omit \
the honorific entirely rather than guessing.
5. No placeholder tokens of any kind ({{name}}, [Company], TBD, <title>, etc.) \
may appear in the output -- every field must be the final text.
6. The follow-up message is sent after the connection is accepted -- it can \
be slightly longer, but stay professional and specific, not generic.
7. Return ONLY the JSON object matching the given schema.
"""


@dataclass(frozen=True)
class DraftedMessages:
    connection_note: str
    follow_up_message: str


def draft_company_messages(
    user_sector: str,
    company: Company,
    contacts: list[Contact],
    api_key: str,
) -> dict[str, DraftedMessages]:
    """Drafts messages for every contact in `contacts` that has a name, in
    one Gemini call. Returns {role: DraftedMessages}. Contacts with no name
    (no Crustdata match) are skipped -- never drafted for."""
    found = [c for c in contacts if c.name]
    if not found:
        return {}

    schema = {
        "type": "OBJECT",
        "properties": {c.role: _MESSAGE_SCHEMA for c in found},
        "required": [c.role for c in found],
    }

    contact_facts = {
        c.role: {k: v for k, v in {"name": c.name, "title": c.title}.items() if v}
        for c in found
    }
    facts = {
        "user_sector": user_sector,
        "target_company_name": company.name,
        "target_company_rationale": company.rationale,
        "contacts": contact_facts,
    }

    prompt = (
        "Draft the connection note and follow-up for each contact below. "
        f"Known facts (use ONLY these, nothing else): {facts}"
    )

    data = generate_json(
        GEMINI_MODELS,
        prompt,
        schema,
        api_key,
        system_instruction=SYSTEM_PROMPT,
    )

    return {
        role: DraftedMessages(
            connection_note=msg["connection_note"],
            follow_up_message=msg["follow_up_message"],
        )
        for role, msg in data.items()
        if role in contact_facts
    }
