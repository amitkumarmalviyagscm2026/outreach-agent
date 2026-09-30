"""Google Custom Search result parsing: extracting a name from a LinkedIn
page title, filtering to real /in/ profile URLs, and picking the most
senior match from snippet text."""
from src.google_search_client import GoogleSearchClient, _extract_name


def test_extract_name_from_typical_linkedin_title():
    assert _extract_name("Priya Sharma - HR Manager - Cipla | LinkedIn") == "Priya Sharma"
    assert _extract_name("Ravi Kumar | LinkedIn") == "Ravi Kumar"


def test_extract_name_rejects_titles_with_digits():
    # Digit-bearing titles ("500+ jobs at Cipla") are never a person's name.
    # Company/jobs pages are filtered separately, by URL shape (/company/
    # vs /in/), not by name content -- see test_find_contact_skips_non_profile_links.
    assert _extract_name("500+ jobs at Cipla | LinkedIn") is None


def test_find_contact_skips_non_profile_links(monkeypatch):
    client = GoogleSearchClient("key", "cx")

    def fake_search(query):
        return [
            {"title": "Cipla - Company Page | LinkedIn", "link": "https://www.linkedin.com/company/cipla", "snippet": "HR Head"},
            {"title": "Priya Sharma - HR Manager - Cipla | LinkedIn", "link": "https://www.linkedin.com/in/priya-sharma-123", "snippet": "HR Manager at Cipla"},
        ]

    monkeypatch.setattr(client, "_search", fake_search)
    contact = client.find_contact("hr_ta", "Cipla", ["HR", "Human Resources"])

    assert contact.name == "Priya Sharma"
    assert contact.linkedin_url == "https://www.linkedin.com/in/priya-sharma-123"
    assert contact.source == "google_search"
    assert contact.title is None  # never store an unverified snippet as a title


def test_find_contact_picks_most_senior_by_snippet(monkeypatch):
    client = GoogleSearchClient("key", "cx")

    def fake_search(query):
        return [
            {"title": "Kavya Rao | LinkedIn", "link": "https://www.linkedin.com/in/kavya-rao", "snippet": "HR Executive at Cipla"},
            {"title": "Anil Mehta | LinkedIn", "link": "https://www.linkedin.com/in/anil-mehta", "snippet": "Head of HR at Cipla"},
        ]

    monkeypatch.setattr(client, "_search", fake_search)
    contact = client.find_contact("hr_ta", "Cipla", ["HR"])
    assert contact.name == "Anil Mehta"


def test_find_contact_excludes_by_snippet():
    client = GoogleSearchClient("key", "cx")

    def fake_search(query):
        return [
            {"title": "Someone | LinkedIn", "link": "https://www.linkedin.com/in/someone", "snippet": "Head of Sales Operations at Cipla"},
        ]

    client._search = fake_search  # type: ignore[method-assign]
    contact = client.find_contact("ops_scm", "Cipla", ["Operations"], exclude=["sales"])
    assert contact.name is None


def test_find_contact_no_results_returns_blank():
    client = GoogleSearchClient("key", "cx")
    client._search = lambda query: []  # type: ignore[method-assign]
    contact = client.find_contact("hr_ta", "Cipla", ["HR"])
    assert contact.name is None
    assert contact.source == "google_search"
