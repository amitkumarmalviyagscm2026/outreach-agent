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
#   - "gemini-flash-latest" (-> Gemini 3.8 Flash) was tried as a fallback
#     alongside Lite, but AI Studio's own Rate Limit dashboard confirmed its
#     free-tier cap is just 5 RPM / 20 RPD -- 20 requests a DAY, total. A
#     single test burst exceeded that (29/20 used) and it then fails on
#     every call until the next daily reset, no matter how well retries are
#     tuned. Useless as a fallback for a pipeline that needs 200+ calls per
#     full run, so it's been removed rather than kept as dead weight.
#   - "gemini-flash-lite-latest" (-> Gemini 3.5 Flash Lite) has a much
#     larger free-tier budget: 15 RPM / 500 RPD, confirmed from the same
#     dashboard with plenty of headroom left after all this session's
#     testing (95/500 RPD used). This is the one model actually sized for
#     this pipeline's volume.
# If Lite's own limits are ever hit, use the PowerShell snippet in
# README.md's troubleshooting section to find another working model name,
# and check its own RPM/RPD on the AI Studio Rate Limit dashboard before
# adding it here -- don't repeat the mistake above.
GEMINI_MODELS = ["gemini-flash-lite-latest"]


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
