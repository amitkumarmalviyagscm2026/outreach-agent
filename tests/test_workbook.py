import os
import tempfile

from openpyxl import load_workbook

from src.workbook import HEADERS, OutputRow, write_workbook


def _sample_row(**overrides) -> OutputRow:
    base = dict(
        company="Cipla Limited",
        hr_name="Priya Sharma",
        hr_title="Head of Talent Acquisition",
        hr_linkedin="https://www.linkedin.com/in/priya-example",
        hr_note="Hi Priya, ...",
        hr_followup="Thanks for connecting, Priya.",
        ops_name=None,
        ops_title=None,
        ops_linkedin=None,
        ops_note=None,
        ops_followup=None,
        qa_flag="",
    )
    base.update(overrides)
    return OutputRow(**base)


def test_writes_headers_and_row():
    with tempfile.TemporaryDirectory() as tmp:
        path = write_workbook([_sample_row()], sector="Pharma", mode="test", output_dir=tmp)
        assert os.path.exists(path)
        assert "_TEST_" in os.path.basename(path)

        wb = load_workbook(path)
        ws = wb.active
        header_row = [c.value for c in ws[1]]
        assert header_row == HEADERS
        assert ws.cell(row=2, column=1).value == "Cipla Limited"


def test_linkedin_cell_is_real_hyperlink_not_bare_text():
    with tempfile.TemporaryDirectory() as tmp:
        path = write_workbook([_sample_row()], sector="Pharma", mode="test", output_dir=tmp)
        wb = load_workbook(path)
        ws = wb.active
        # HR/TA LinkedIn column is column 4
        cell = ws.cell(row=2, column=4)
        assert cell.hyperlink is not None
        assert cell.hyperlink.target == "https://www.linkedin.com/in/priya-example"


def test_missing_contact_leaves_cells_blank_not_broken_link():
    with tempfile.TemporaryDirectory() as tmp:
        path = write_workbook([_sample_row()], sector="Pharma", mode="test", output_dir=tmp)
        wb = load_workbook(path)
        ws = wb.active
        # Ops/SCM name column is column 7 -- no ops contact in this fixture
        cell = ws.cell(row=2, column=7)
        assert cell.hyperlink is None
        # openpyxl round-trips an empty string as None on reload -- both
        # mean "nothing written", which is what we're asserting here.
        assert cell.value in (None, "")


def test_full_mode_filename_has_no_test_suffix():
    with tempfile.TemporaryDirectory() as tmp:
        path = write_workbook([_sample_row()], sector="Pharma", mode="full", output_dir=tmp)
        assert "_TEST" not in os.path.basename(path)
