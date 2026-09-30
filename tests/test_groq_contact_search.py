"""groq_contact_search.find_contact(): tolerant JSON parsing of the
model's free-text response, and the claimed LinkedIn URL is only trusted
when it matches a URL Groq's browser_search tool actually surfaced."""
import src.groq_contact_search as mod


def _patch(monkeypatch, text, sources):
    def fake(prompt, api_key, system_instruction=None):
        return text, sources
    monkeypatch.setattr(mod, "chat_with_browser_search", fake)


def test_verified_contact_is_returned(monkeypatch):
    _patch(
        monkeypatch,
        '{"found": true, "name": "Priya Sharma", "title": "HR Manager", '
        '"linkedin_url": "https://www.linkedin.com/in/priya"}',
        ["https://www.linkedin.com/in/priya"],
    )
    contact = mod.find_contact("hr_ta", "Acme", ["HR"], "key")
    assert contact.name == "Priya Sharma"
    assert contact.source == "groq_search"


def test_unverified_url_is_discarded(monkeypatch):
    _patch(
        monkeypatch,
        '{"found": true, "name": "Priya Sharma", "title": "HR Manager", '
        '"linkedin_url": "https://www.linkedin.com/in/priya"}',
        ["https://example.com/unrelated"],
    )
    contact = mod.find_contact("hr_ta", "Acme", ["HR"], "key")
    assert contact.name is None


def test_found_false_returns_blank(monkeypatch):
    _patch(monkeypatch, '{"found": false}', [])
    contact = mod.find_contact("hr_ta", "Acme", ["HR"], "key")
    assert contact.name is None
    assert contact.source == "groq_search"


def test_unparseable_response_returns_blank(monkeypatch):
    _patch(monkeypatch, "not json at all", [])
    contact = mod.find_contact("hr_ta", "Acme", ["HR"], "key")
    assert contact.name is None


def test_excluded_title_is_rejected(monkeypatch):
    _patch(
        monkeypatch,
        '{"found": true, "name": "Rohan", "title": "HR Intern", '
        '"linkedin_url": "https://www.linkedin.com/in/rohan"}',
        ["https://www.linkedin.com/in/rohan"],
    )
    contact = mod.find_contact("hr_ta", "Acme", ["HR"], "key", exclude=["Intern"])
    assert contact.name is None


def test_runtime_error_returns_blank(monkeypatch):
    def raising(prompt, api_key, system_instruction=None):
        raise RuntimeError("Groq call failed on all models")
    monkeypatch.setattr(mod, "chat_with_browser_search", raising)

    contact = mod.find_contact("hr_ta", "Acme", ["HR"], "key")
    assert contact.name is None
    assert contact.source == "groq_search"


def test_markdown_fenced_json_is_parsed(monkeypatch):
    _patch(
        monkeypatch,
        '```json\n{"found": true, "name": "Priya", "title": "HR", '
        '"linkedin_url": "https://www.linkedin.com/in/priya"}\n```',
        ["https://www.linkedin.com/in/priya"],
    )
    contact = mod.find_contact("hr_ta", "Acme", ["HR"], "key")
    assert contact.name == "Priya"
