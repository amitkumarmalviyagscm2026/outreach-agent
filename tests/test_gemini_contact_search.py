"""Gemini Search grounding fallback: tolerant JSON parsing of the model's
free-text response, and -- the important part -- that a claimed LinkedIn
URL is only trusted when it matches an actual search citation, never on
the model's word alone."""
import src.gemini_contact_search as mod
from src.gemini_contact_search import _parse_json_loosely, _url_is_verified, find_contact


def test_parses_clean_json():
    assert _parse_json_loosely('{"found": true, "name": "Priya"}') == {"found": True, "name": "Priya"}


def test_parses_json_wrapped_in_markdown_fence():
    text = '```json\n{"found": true, "name": "Priya"}\n```'
    assert _parse_json_loosely(text) == {"found": True, "name": "Priya"}


def test_parses_json_with_surrounding_prose():
    text = 'Here is what I found:\n{"found": true, "name": "Priya"}\nLet me know if you need more.'
    assert _parse_json_loosely(text)["name"] == "Priya"


def test_unparseable_text_returns_none():
    assert _parse_json_loosely("I couldn't find anyone matching that.") is None


def test_url_verified_when_it_matches_a_citation():
    sources = [{"uri": "https://www.linkedin.com/in/priya-sharma-123", "title": "Priya Sharma"}]
    assert _url_is_verified("https://linkedin.com/in/priya-sharma-123/", sources) is True


def test_url_not_verified_when_no_citation_matches():
    sources = [{"uri": "https://www.crustdata.com/about", "title": "Crustdata"}]
    assert _url_is_verified("https://www.linkedin.com/in/priya-sharma-123", sources) is False


def test_url_not_verified_when_no_sources_at_all():
    # The critical case: a model that claims a URL with nothing to back it
    # up must never be trusted, even if the URL itself looks plausible.
    assert _url_is_verified("https://www.linkedin.com/in/priya-sharma-123", []) is False


def test_find_contact_rejects_unverified_claim(monkeypatch):
    monkeypatch.setattr(
        mod,
        "generate_grounded_text",
        lambda models, prompt, key, **kw: (
            '{"found": true, "name": "Priya Sharma", "title": "HR Head", '
            '"linkedin_url": "https://www.linkedin.com/in/made-up-slug"}',
            [{"uri": "https://example.com/unrelated-page", "title": "Unrelated"}],
        ),
    )
    contact = find_contact("hr_ta", "Cipla", ["HR"], "key", ["model"])
    assert contact.name is None
    assert contact.source == "gemini_search"


def test_find_contact_accepts_verified_claim(monkeypatch):
    monkeypatch.setattr(
        mod,
        "generate_grounded_text",
        lambda models, prompt, key, **kw: (
            '{"found": true, "name": "Priya Sharma", "title": "HR Head", '
            '"linkedin_url": "https://www.linkedin.com/in/priya-sharma"}',
            [{"uri": "https://www.linkedin.com/in/priya-sharma", "title": "Priya Sharma - HR Head"}],
        ),
    )
    contact = find_contact("hr_ta", "Cipla", ["HR"], "key", ["model"])
    assert contact.name == "Priya Sharma"
    assert contact.linkedin_url == "https://www.linkedin.com/in/priya-sharma"
    assert contact.source == "gemini_search"


def test_find_contact_respects_found_false(monkeypatch):
    monkeypatch.setattr(
        mod, "generate_grounded_text", lambda models, prompt, key, **kw: ('{"found": false}', [])
    )
    contact = find_contact("hr_ta", "Cipla", ["HR"], "key", ["model"])
    assert contact.name is None


def test_find_contact_applies_exclude_list(monkeypatch):
    monkeypatch.setattr(
        mod,
        "generate_grounded_text",
        lambda models, prompt, key, **kw: (
            '{"found": true, "name": "Someone", "title": "Sales Operations Manager", '
            '"linkedin_url": "https://www.linkedin.com/in/someone"}',
            [{"uri": "https://www.linkedin.com/in/someone", "title": "Someone"}],
        ),
    )
    contact = find_contact("ops_scm", "Cipla", ["Operations"], "key", ["model"], exclude=["sales"])
    assert contact.name is None


def test_find_contact_handles_api_failure_gracefully(monkeypatch):
    def raise_error(models, prompt, key, **kw):
        raise RuntimeError("Gemini grounded call failed on all models")

    monkeypatch.setattr(mod, "generate_grounded_text", raise_error)
    contact = find_contact("hr_ta", "Cipla", ["HR"], "key", ["model"])
    assert contact.name is None
    assert contact.source == "gemini_search"
