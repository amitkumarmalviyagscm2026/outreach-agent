"""ContactSearchService routing: Crustdata first, Gemini Search grounding
fallback only after Crustdata signals an exhausted balance, and only for
the rest of the run from that point on."""
from src.contact_search import ContactSearchService
from src.crustdata_client import Contact, CrustdataExhausted


class FakeCrustdata:
    def __init__(self, *a, **kw):
        self.calls: list[str] = []
        self.exhausted_after = None  # company name after which it starts raising
        self._exhausted = False
        self.counter = type("C", (), {"summary": lambda self: "crustdata summary"})()

    def get_two_contacts(self, company_name):
        self.calls.append(company_name)
        if self._exhausted or company_name == self.exhausted_after:
            self._exhausted = True
            raise CrustdataExhausted("balance exhausted")
        return (
            Contact(role="hr_ta", name="Priya", linkedin_url="https://linkedin.com/in/priya"),
            Contact(role="ops_scm", name="Ravi", linkedin_url="https://linkedin.com/in/ravi"),
        )

    def close(self):
        pass


def _service(monkeypatch):
    import src.contact_search as mod

    fake_crustdata = FakeCrustdata()
    monkeypatch.setattr(mod, "CrustdataClient", lambda key: fake_crustdata)

    gemini_calls: list[tuple[str, str]] = []

    def fake_gemini_find(role, company_name, keywords, api_key, models, exclude=None):
        gemini_calls.append((role, company_name))
        return Contact(role=role, name=f"Gemini-{role}", linkedin_url="https://linkedin.com/in/x", source="gemini_search")

    monkeypatch.setattr(mod, "gemini_find_contact", fake_gemini_find)

    svc = ContactSearchService("crust-key", "gemini-key", ["gemini-flash-lite-latest"])
    return svc, fake_crustdata, gemini_calls


def test_crustdata_used_while_it_has_balance(monkeypatch):
    svc, crustdata, gemini_calls = _service(monkeypatch)
    hr, ops = svc.get_two_contacts("Cipla")
    assert hr.name == "Priya" and hr.source == "crustdata"
    assert gemini_calls == []


def test_falls_back_to_gemini_after_crustdata_exhausted(monkeypatch):
    svc, crustdata, gemini_calls = _service(monkeypatch)
    crustdata.exhausted_after = "Sun Pharma"

    svc.get_two_contacts("Cipla")  # works fine
    hr, ops = svc.get_two_contacts("Sun Pharma")  # exhausts here, falls back same company
    assert hr.source == "gemini_search"
    assert gemini_calls == [("hr_ta", "Sun Pharma"), ("ops_scm", "Sun Pharma")]

    gemini_calls.clear()
    svc.get_two_contacts("Zydus")  # Crustdata skipped entirely from now on
    assert crustdata.calls == ["Cipla", "Sun Pharma"]  # never called again for Zydus
    assert gemini_calls == [("hr_ta", "Zydus"), ("ops_scm", "Zydus")]


def test_summary_reports_both_sources_once_fallback_used(monkeypatch):
    svc, crustdata, gemini_calls = _service(monkeypatch)
    crustdata.exhausted_after = "Cipla"
    svc.get_two_contacts("Cipla")

    summary = svc.summary()
    assert "crustdata summary" in summary
    assert "Gemini Search fallback" in summary
    assert "2 calls" in summary  # hr_ta + ops_scm
    assert "2 contacts found" in summary


def test_summary_omits_gemini_section_when_never_used(monkeypatch):
    svc, crustdata, gemini_calls = _service(monkeypatch)
    svc.get_two_contacts("Cipla")  # Crustdata has balance, fallback never triggered

    assert "Gemini Search fallback" not in svc.summary()
