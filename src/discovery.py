"""Sector -> ranked top-N company list, via one Gemini structured-output call.

This stage names companies only. It must never invent contact facts --
those come exclusively from Crustdata in crustdata_client.py.

Each company carries two names:
  - name: the official/full name, for display in the workbook
  - search_name: the short common name people list as their employer on
    LinkedIn (e.g. "Procter & Gamble", not "Procter & Gamble Hygiene and
    Health Care Limited"). Crustdata's "(.)" match requires EVERY word of
    the filter value to appear in the stored employer name, so a long
    legal name silently zero-matches -- a real run lost P&G entirely this
    way. search_name is what gets sent to Crustdata.
"""
from __future__ import annotations

from dataclasses import dataclass

from src.config import GEMINI_MODELS
from src.gemini_client import generate_json

COMPANY_LIST_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "companies": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "name": {
                        "type": "STRING",
                        "description": "Official company name.",
                    },
                    "search_name": {
                        "type": "STRING",
                        "description": (
                            "The short, common name employees would list as their employer "
                            "on LinkedIn -- no legal suffixes (Limited, Ltd, Pvt, Inc) and no "
                            "subsidiary descriptors. E.g. 'Cipla' not 'Cipla Limited', "
                            "'Procter & Gamble' not 'Procter & Gamble Hygiene and Health Care Limited'."
                        ),
                    },
                    "rationale": {
                        "type": "STRING",
                        "description": "One short phrase on why this company is a relevant campus-hiring target in this sector.",
                    },
                },
                "required": ["name", "search_name", "rationale"],
            },
        }
    },
    "required": ["companies"],
}

# ~60 output tokens per company (name + search_name + rationale + JSON
# overhead), so 100 companies needs ~6K. The previous 4096 cap would
# truncate a 100-company list mid-JSON, fail to parse, and fail identically
# on every retry.
_DISCOVERY_MAX_OUTPUT_TOKENS = 16384


@dataclass(frozen=True)
class Company:
    name: str
    search_name: str
    rationale: str


def rank_companies(sector: str, count: int, api_key: str) -> list[Company]:
    """Asks Gemini for exactly `count` companies in `sector`, ranked by
    relevance as a campus-hiring / business-development target."""
    prompt = (
        f"List the top {count} companies in the '{sector}' sector "
        "in India, ranked by relevance as a target for MBA campus "
        "hiring outreach / partnership development. Prefer large, "
        "well-known employers with active hiring or business "
        "development functions. Keep each rationale to a short phrase. "
        f"Return exactly {count} companies, no more, no fewer."
    )

    data = generate_json(
        GEMINI_MODELS,
        prompt,
        COMPANY_LIST_SCHEMA,
        api_key,
        max_output_tokens=_DISCOVERY_MAX_OUTPUT_TOKENS,
    )
    raw = data.get("companies", [])
    companies = [
        Company(
            name=c["name"],
            search_name=c.get("search_name") or c["name"],
            rationale=c["rationale"],
        )
        for c in raw
    ]

    if len(companies) != count:
        print(
            f"WARNING: requested {count} companies, model returned "
            f"{len(companies)}. Proceeding with what was returned."
        )
    return companies
