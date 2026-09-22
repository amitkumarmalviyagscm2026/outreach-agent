"""drafting.py now builds messages from fixed templates (templates.py),
not an LLM call -- these tests exercise that wiring end to end."""
from src.crustdata_client import Contact
from src.discovery import Company
from src.drafting import draft_company_messages

COMPANY = Company(name="Cipla Limited", search_name="Cipla", rationale="large pharma")


def test_drafts_only_contacts_that_have_a_name():
    hr = Contact(role="hr_ta", name="Priya Sharma", title="HR Head",
                 linkedin_url="https://www.linkedin.com/in/priya")
    ops = Contact(role="ops_scm")  # no match found

    out = draft_company_messages("Pharma", COMPANY, [hr, ops])

    assert set(out.keys()) == {"hr_ta"}
    assert "Priya" in out["hr_ta"].connection_note
    assert "Cipla" in out["hr_ta"].follow_up_message


def test_no_contacts_returns_empty():
    assert draft_company_messages("Pharma", COMPANY, [Contact(role="hr_ta"), Contact(role="ops_scm")]) == {}


def test_hr_ta_and_ops_scm_get_different_wording():
    hr = Contact(role="hr_ta", name="Priya Sharma", title="HR Head")
    ops = Contact(role="ops_scm", name="Ravi Kumar", title="Plant Head")

    out = draft_company_messages("Pharma", COMPANY, [hr, ops])

    assert out["hr_ta"].connection_note != out["ops_scm"].connection_note
    assert "Supply Chain" in out["ops_scm"].connection_note
    assert "Placement Committee" in out["hr_ta"].connection_note
