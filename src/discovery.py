"""Sector -> ranked top-N company list, via one Anthropic tool-use call.

This stage names companies only. It must never invent contact facts --
those come exclusively from Crustdata in crustdata_client.py.
"""
from __future__ import annotations

from dataclasses import dataclass

import anthropic

from src.config import ANTHROPIC_MODEL

COMPANY_LIST_TOOL = {
    "name": "return_company_list",
    "description": "Return the ranked list of top companies for the given sector.",
    "input_schema": {
        "type": "object",
        "properties": {
            "companies": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "Official company name, suitable for a people-search lookup.",
                        },
                        "rationale": {
                            "type": "string",
                            "description": "One short phrase on why this company is a relevant campus-hiring target in this sector.",
                        },
                    },
                    "required": ["name", "rationale"],
                },
            }
        },
        "required": ["companies"],
    },
}


@dataclass(frozen=True)
class Company:
    name: str
    rationale: str


def rank_companies(sector: str, count: int, api_key: str) -> list[Company]:
    """Asks Claude for exactly `count` companies in `sector`, ranked by
    relevance as a campus-hiring / business-development target. Forces
    structured JSON via tool-use rather than parsing free text markdown --
    avoids brittle parsing and half-formed lists."""
    client = anthropic.Anthropic(api_key=api_key)

    message = client.messages.create(
        model=ANTHROPIC_MODEL,
        max_tokens=4096,
        tools=[COMPANY_LIST_TOOL],
        tool_choice={"type": "tool", "name": "return_company_list"},
        messages=[
            {
                "role": "user",
                "content": (
                    f"List the top {count} companies in the '{sector}' sector "
                    "in India, ranked by relevance as a target for MBA campus "
                    "hiring outreach / partnership development. Prefer large, "
                    "well-known employers with active hiring or business "
                    "development functions. Use each company's official "
                    "registered/trading name. Return exactly "
                    f"{count} companies, no more, no fewer."
                ),
            }
        ],
    )

    for block in message.content:
        if block.type == "tool_use" and block.name == "return_company_list":
            raw = block.input.get("companies", [])
            companies = [Company(name=c["name"], rationale=c["rationale"]) for c in raw]
            if len(companies) != count:
                print(
                    f"WARNING: requested {count} companies, model returned "
                    f"{len(companies)}. Proceeding with what was returned."
                )
            return companies

    raise RuntimeError("discovery: model did not return a structured company list")
