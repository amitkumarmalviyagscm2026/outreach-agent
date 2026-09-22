"""Over-length connection notes get one rewrite; if that fails or is still
too long, the original is kept for the QA gate to flag -- never silently
truncated mid-sentence."""
from src import drafting
from src.config import CONNECTION_NOTE_MAX_CHARS
from src.crustdata_client import Contact
from src.discovery import Company

LONG = "x" * (CONNECTION_NOTE_MAX_CHARS + 40)
COMPANY = Company(name="Cipla Limited", search_name="Cipla", rationale="large pharma")
HR = Contact(role="hr_ta", name="Priya Sharma", title="HR Head")


def _fake_generate(responses):
    calls = []

    def fake(models, prompt, schema, api_key, **kwargs):
        calls.append(prompt)
        return responses[len(calls) - 1]

    return fake, calls


def test_short_note_is_not_rewritten(monkeypatch):
    fake, calls = _fake_generate([
        {"hr_ta": {"connection_note": "Hi Priya, keen to connect.", "follow_up_message": "Thanks!"}},
    ])
    monkeypatch.setattr(drafting, "generate_json", fake)
    out = drafting.draft_company_messages("Pharma", COMPANY, [HR], "key")
    assert out["hr_ta"].connection_note == "Hi Priya, keen to connect."
    assert len(calls) == 1  # no extra shortening call


def test_long_note_is_shortened(monkeypatch):
    fake, calls = _fake_generate([
        {"hr_ta": {"connection_note": LONG, "follow_up_message": "Thanks!"}},
        {"connection_note": "Hi Priya, shorter now."},
    ])
    monkeypatch.setattr(drafting, "generate_json", fake)
    out = drafting.draft_company_messages("Pharma", COMPANY, [HR], "key")
    assert out["hr_ta"].connection_note == "Hi Priya, shorter now."
    assert out["hr_ta"].follow_up_message == "Thanks!"
    assert len(calls) == 2


def test_rewrite_still_too_long_keeps_original_for_qa(monkeypatch):
    fake, _ = _fake_generate([
        {"hr_ta": {"connection_note": LONG, "follow_up_message": "Thanks!"}},
        {"connection_note": LONG + "y"},
    ])
    monkeypatch.setattr(drafting, "generate_json", fake)
    out = drafting.draft_company_messages("Pharma", COMPANY, [HR], "key")
    assert out["hr_ta"].connection_note == LONG


def test_failed_rewrite_keeps_original_for_qa(monkeypatch):
    responses = [{"hr_ta": {"connection_note": LONG, "follow_up_message": "Thanks!"}}]
    calls = []

    def fake(models, prompt, schema, api_key, **kwargs):
        calls.append(prompt)
        if len(calls) == 1:
            return responses[0]
        raise RuntimeError("Gemini call failed on all models: 503")

    monkeypatch.setattr(drafting, "generate_json", fake)
    out = drafting.draft_company_messages("Pharma", COMPANY, [HR], "key")
    assert out["hr_ta"].connection_note == LONG
