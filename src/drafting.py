"""Per-company message drafting: builds the connection note + follow-up
for each contact found at that company, from the fixed templates in
templates.py.

This no longer calls an LLM at all -- see templates.py for why: the
message content is real, exact facts about the sender's own program
(batch size, rankings, placement numbers), not something to have an LLM
compose or paraphrase. Removing the LLM call here also removes drafting's
exposure to Gemini/Groq rate limits entirely; the LLM is now only used by
discovery.py for the company list.
"""
from __future__ import annotations

from dataclasses import dataclass

from src.crustdata_client import Contact
from src.discovery import Company
from src.templates import build_connection_note, build_follow_up


@dataclass(frozen=True)
class DraftedMessages:
    connection_note: str
    follow_up_message: str


def draft_company_messages(
    user_sector: str,
    company: Company,
    contacts: list[Contact],
) -> dict[str, DraftedMessages]:
    """Builds messages for every contact in `contacts` that has a name.
    Contacts with no name (no Crustdata match) are skipped -- never
    drafted for. `user_sector` is accepted for interface stability with
    the pipeline but isn't used by the fixed template."""
    del user_sector  # not used by the fixed template -- kept for call-site stability
    found = [c for c in contacts if c.name]
    if not found:
        return {}

    return {
        c.role: DraftedMessages(
            connection_note=build_connection_note(c.name, c.role),
            follow_up_message=build_follow_up(c.name, c.title, company.name, c.role),
        )
        for c in found
    }
