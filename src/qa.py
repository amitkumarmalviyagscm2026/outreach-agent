"""Validation gate, mirroring the checks proven out in interactive runs.

Failing rows are never silently dropped or silently trusted -- they get a
QA_FLAG explaining what looked wrong, so a human reviews before sending.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from src.config import CONNECTION_NOTE_MAX_CHARS

COMBINED_SALUTATION_PATTERNS = [
    re.compile(r"\bSir\s*/\s*Ma'?am\b", re.IGNORECASE),
    re.compile(r"\bSir\s+or\s+Ma'?am\b", re.IGNORECASE),
    re.compile(r"\bDear\s+Sir/Madam\b", re.IGNORECASE),
]

PLACEHOLDER_PATTERNS = [
    re.compile(r"\{[a-zA-Z_]+\}"),          # {name}
    re.compile(r"\[[A-Za-z ]+\]"),          # [Company]
    re.compile(r"<[a-zA-Z_]+>"),            # <title>
    re.compile(r"\bTBD\b"),
    re.compile(r"\bGSCM\b"),                # unexpanded abbreviation
]

LINKEDIN_URL_PATTERN = re.compile(r"^https://(www\.)?linkedin\.com/.+", re.IGNORECASE)

# A bare-initial greeting ("Hi B," / "Hi K,") reads as broken -- caught as a
# real bug in an earlier interactive run; kept here permanently.
BARE_INITIAL_GREETING = re.compile(r"\bHi\s+[A-Z]\.?,", re.IGNORECASE)

DOUBLE_PUNCTUATION = re.compile(r"([,.])\1|(  )")


@dataclass
class QAResult:
    ok: bool
    flags: list[str]


def _check_text(label: str, text: str | None, flags: list[str]) -> None:
    if not text:
        return
    if len(text) > CONNECTION_NOTE_MAX_CHARS and label == "connection_note":
        flags.append(f"{label}: {len(text)} chars, exceeds {CONNECTION_NOTE_MAX_CHARS}")
    for pat in COMBINED_SALUTATION_PATTERNS:
        if pat.search(text):
            flags.append(f"{label}: combined salutation (Sir/Ma'am pattern)")
            break
    for pat in PLACEHOLDER_PATTERNS:
        if pat.search(text):
            flags.append(f"{label}: leftover placeholder token")
            break
    if BARE_INITIAL_GREETING.search(text):
        flags.append(f"{label}: greeting uses a bare initial")
    if DOUBLE_PUNCTUATION.search(text):
        flags.append(f"{label}: doubled punctuation or double space")


def validate_contact_messages(
    connection_note: str | None,
    follow_up_message: str | None,
    linkedin_url: str | None,
) -> QAResult:
    flags: list[str] = []
    _check_text("connection_note", connection_note, flags)
    _check_text("follow_up_message", follow_up_message, flags)

    if linkedin_url and not LINKEDIN_URL_PATTERN.match(linkedin_url):
        flags.append("linkedin_url: does not look like a valid LinkedIn URL")

    if not connection_note and not follow_up_message and linkedin_url:
        # A contact with a profile URL but no drafted messages is an orphan
        # -- the drafting step must have failed silently somewhere upstream.
        flags.append("orphan: linkedin_url present but no messages drafted")

    return QAResult(ok=len(flags) == 0, flags=flags)
