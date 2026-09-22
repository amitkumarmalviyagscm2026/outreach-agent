"""Sector -> ranked top-N company list, via one Gemini structured-output call.

This stage names companies only. It must never invent contact facts --
those come exclusively from Crustdata in crustdata_client.py.
"""
from __future__ import annotations

from dataclasses import dataclass

from src.config import GEMINI_MODEL
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
                        "description": "Official company name, suitable for a people-search lookup.",
                    },
                    "rationale": {
                        "type": "STRING",
                        "description": "One short phrase on why this company is a relevant campus-hiring target in this sector.",
                    },
                },
                "required": ["name", "rationale"],
            },
        }
    },
    "required": ["companies"],
}


@dataclass(frozen=True)
class Company:
    name: str
    rationale: str


def rank_companies(sector: str, count: int, api_key: str) -> list[Company]:
    """Asks Gemini for exactly `count` companies in `sector`, ranked by
    relevance as a campus-hiring / business-development target. Forces
    structured JSON via response_schema rather than parsing free text
    markdown -- avoids brittle parsing and half-formed lists."""
    prompt = (
        f"List the top {count} companies in the '{sector}' sector "
        "in India, ranked by relevance as a target for MBA campus "
        "hiring outreach / partnership development. Prefer large, "
        "well-known employers with active hiring or business "
        "development functions. Use each company's official "
        "registered/trading name. Return exactly "
        f"{count} companies, no more, no fewer."
    )

    data = generate_json(GEMINI_MODEL, prompt, COMPANY_LIST_SCHEMA, api_key)
    raw = data.get("companies", [])
    companies = [Company(name=c["name"], rationale=c["rationale"]) for c in raw]

    if len(companies) != count:
        print(
            f"WARNING: requested {count} companies, model returned "
            f"{len(companies)}. Proceeding with what was returned."
        )
    return companies
