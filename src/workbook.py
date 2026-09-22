"""xlsx writer. LinkedIn URLs become real clickable cell hyperlinks -- never
bare text -- per the user's standing preference from interactive runs."""
from __future__ import annotations

import datetime as dt
import os
from dataclasses import dataclass

from openpyxl import Workbook
from openpyxl.worksheet.worksheet import Worksheet

HEADERS = [
    "Company",
    "HR/TA Name", "HR/TA Title", "HR/TA LinkedIn",
    "HR/TA Connection Note", "HR/TA Follow-up",
    "Ops/SCM Name", "Ops/SCM Title", "Ops/SCM LinkedIn",
    "Ops/SCM Connection Note", "Ops/SCM Follow-up",
    "QA Flag",
]

COLUMN_WIDTHS = [28, 20, 26, 34, 40, 40, 20, 26, 34, 40, 40, 40]


@dataclass
class OutputRow:
    company: str
    hr_name: str | None
    hr_title: str | None
    hr_linkedin: str | None
    hr_note: str | None
    hr_followup: str | None
    ops_name: str | None
    ops_title: str | None
    ops_linkedin: str | None
    ops_note: str | None
    ops_followup: str | None
    qa_flag: str


def _write_link_cell(ws: Worksheet, row: int, col: int, display_text: str | None, url: str | None) -> None:
    cell = ws.cell(row=row, column=col)
    if url:
        cell.value = display_text or url
        cell.hyperlink = url
        cell.style = "Hyperlink"
    else:
        cell.value = display_text or ""


def write_workbook(rows: list[OutputRow], sector: str, mode: str, output_dir: str = "output") -> str:
    os.makedirs(output_dir, exist_ok=True)

    wb = Workbook()
    ws = wb.active
    ws.title = "Outreach"

    for col_idx, header in enumerate(HEADERS, start=1):
        ws.cell(row=1, column=col_idx, value=header)
    ws.freeze_panes = "A2"
    for col_idx, width in enumerate(COLUMN_WIDTHS, start=1):
        ws.column_dimensions[ws.cell(row=1, column=col_idx).column_letter].width = width

    for r, row in enumerate(rows, start=2):
        ws.cell(row=r, column=1, value=row.company)
        _write_link_cell(ws, r, 2, row.hr_name, row.hr_linkedin)
        ws.cell(row=r, column=3, value=row.hr_title or "")
        _write_link_cell(ws, r, 4, row.hr_linkedin, row.hr_linkedin)
        ws.cell(row=r, column=5, value=row.hr_note or "")
        ws.cell(row=r, column=6, value=row.hr_followup or "")
        _write_link_cell(ws, r, 7, row.ops_name, row.ops_linkedin)
        ws.cell(row=r, column=8, value=row.ops_title or "")
        _write_link_cell(ws, r, 9, row.ops_linkedin, row.ops_linkedin)
        ws.cell(row=r, column=10, value=row.ops_note or "")
        ws.cell(row=r, column=11, value=row.ops_followup or "")
        ws.cell(row=r, column=12, value=row.qa_flag or "")

    timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_sector = "".join(c if c.isalnum() else "_" for c in sector)
    suffix = "_TEST" if mode == "test" else ""
    filename = f"outreach_{safe_sector}{suffix}_{timestamp}.xlsx"
    path = os.path.join(output_dir, filename)
    wb.save(path)
    return path
