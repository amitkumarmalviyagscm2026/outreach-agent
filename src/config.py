"""Secrets and run configuration.

Loads CRUSTDATA_API_KEY / GEMINI_API_KEY from the environment. In GitHub
Actions these arrive as real env vars from repo Secrets. For local runs,
loads a .env file (gitignored) if present. Never logs a key's value -- only
whether it is present, so a misconfigured run fails loudly without leaking
anything into the job log.

GEMINI_API_KEY is a Google AI Studio key (https://aistudio.google.com/apikey)
-- genuinely free, no credit card required, used instead of a paid LLM API
so this pipeline costs nothing to run on the drafting/discovery side.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass

# Only touch .env locally -- GITHUB_ACTIONS is set to "true" by the runner,
# and secrets are already real env vars there, so skip dotenv entirely.
if os.environ.get("GITHUB_ACTIONS") != "true":
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass

# --- Fixed pipeline constants (see plan: exactly 2 roles per company) -----
CONNECTION_NOTE_MAX_CHARS = 300
ROLE_HR_TA = "hr_ta"
ROLE_OPS_SCM = "ops_scm"
ROLES = (ROLE_HR_TA, ROLE_OPS_SCM)

TEST_MODE_MAX_COMPANIES = 10
FULL_MODE_MAX_COMPANIES = 100

# Free-tier models, tried in order until one responds (see gemini_client.py).
# History:
#   - "gemini-2.5-flash" (pinned): listed as available by GET /v1beta/models
#     but 404'd on generateContent -- a known, unresolved Google-side quirk.
#     Lesson: use a "-latest" alias, never a pinned dotted version.
#   - A single model, whichever one, was not enough: real runs showed BOTH
#     "gemini-flash-latest" and "gemini-flash-lite-latest" hitting sustained
#     429/503 at different times -- free-tier capacity pressure that moves
#     around, not a single bad model choice. Trying both in order, with a
#     real retry budget on each, is more robust than betting on one.
# If both are ever unavailable, use the PowerShell snippet in README.md's
# troubleshooting section to find what your key can actually call, and add
# it to this list.
GEMINI_MODELS = ["gemini-flash-lite-latest", "gemini-flash-latest"]


@dataclass(frozen=True)
class Secrets:
    crustdata_api_key: str
    gemini_api_key: str


def load_secrets() -> Secrets:
    """Reads required keys from the environment. Exits with a clear,
    secret-free error message if either is missing -- fail fast rather than
    let the pipeline half-run and burn calls before hitting an auth error."""
    crustdata_key = os.environ.get("CRUSTDATA_API_KEY", "").strip()
    gemini_key = os.environ.get("GEMINI_API_KEY", "").strip()

    missing = []
    if not crustdata_key:
        missing.append("CRUSTDATA_API_KEY")
    if not gemini_key:
        missing.append("GEMINI_API_KEY")

    if missing:
        print(
            f"ERROR: missing required secret(s): {', '.join(missing)}. "
            "Set them as GitHub Actions secrets, or in a local .env file "
            "(see .env.example).",
            file=sys.stderr,
        )
        sys.exit(1)

    return Secrets(crustdata_api_key=crustdata_key, gemini_api_key=gemini_key)


def clamp_company_count(requested: int, mode: str) -> int:
    """mode='test' always caps at TEST_MODE_MAX_COMPANIES regardless of what
    was requested -- this is the primary cost-control guardrail and must
    hold even if the Actions form input is mistyped or this function is
    called directly from a script bypassing the workflow layer."""
    if mode == "test":
        return max(1, min(requested, TEST_MODE_MAX_COMPANIES))
    return max(1, min(requested, FULL_MODE_MAX_COMPANIES))
