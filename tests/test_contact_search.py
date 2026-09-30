"""ContactSearchService routing: Crustdata first, Google Search fallback
only after Crustdata signals an exhausted balance, and only for the rest
of the run from that point on."""
from src.contact_search import ContactSearchService
from src.crustdata_client import Contact, CrustdataExhausted
from src.google_search_client import GoogleSearchQuotaExceeded


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


class FakeGoogle:
    def __init__(self, *a, **kw):
        self.calls: list[tuple[str, str]] = []
        self.queries_made = 0
        self.results_returned = 0
        self.raise_quota_exceeded = False

    def find_contact(self, role, company_name, keywords, exclude=None):
        self.calls.append((role, company_name))
        if self.raise_quota_exceeded:
            raise GoogleSearchQuotaExceeded("quota used up")
        return Contact(role=role, name=f"Google-{role}", linkedin_url="https://linkedin.com/in/x", source="google_search")

    def close(self):
        pass


def _service(monkeypatch, with_google=True):
    import src.contact_search as mod

    fake_crustdata = FakeCrustdata()
    monkeypatch.setattr(mod, "CrustdataClient", lambda key: fake_crustdata)
    fake_google = FakeGoogle()
    if with_google:
        monkeypatch.setattr(mod, "GoogleSearchClient", lambda key, cx: fake_google)
        svc = ContactSearchService("crust-key", "google-key", "cx-id")
    else:
        svc = ContactSearchService("crust-key", None, None)
    return svc, fake_crustdata, fake_google


def test_crustdata_used_while_it_has_balance(monkeypatch):
    svc, crustdata, google = _service(monkeypatch)
    hr, ops = svc.get_two_contacts("Cipla")
    assert hr.name == "Priya" and hr.source == "crustdata"
    assert google.calls == []


def test_falls_back_to_google_after_crustdata_exhausted(monkeypatch):
    svc, crustdata, google = _service(monkeypatch)
    crustdata.exhausted_after = "Sun Pharma"

    svc.get_two_contacts("Cipla")  # works fine
    hr, ops = svc.get_two_contacts("Sun Pharma")  # exhausts here, falls back same company
    assert hr.source == "google_search"
    assert google.calls == [("hr_ta", "Sun Pharma"), ("ops_scm", "Sun Pharma")]

    google.calls.clear()
    svc.get_two_contacts("Zydus")  # Crustdata skipped entirely from now on
    assert crustdata.calls == ["Cipla", "Sun Pharma"]  # never called again for Zydus
    assert google.calls == [("hr_ta", "Zydus"), ("ops_scm", "Zydus")]


def test_no_google_configured_leaves_contacts_blank_after_exhaustion(monkeypatch):
    svc, crustdata, google = _service(monkeypatch, with_google=False)
    crustdata.exhausted_after = "Cipla"

    hr, ops = svc.get_two_contacts("Cipla")
    assert hr.name is None and ops.name is None


def test_google_quota_exceeded_stops_fallback_for_rest_of_run(monkeypatch):
    svc, crustdata, google = _service(monkeypatch)
    crustdata.exhausted_after = "Cipla"
    google.raise_quota_exceeded = True

    hr, ops = svc.get_two_contacts("Cipla")
    assert hr.name is None  # the exception was caught, not propagated

    google.calls.clear()
    google.raise_quota_exceeded = False  # even if it would work now, it's skipped
    hr2, ops2 = svc.get_two_contacts("Sun Pharma")
    assert hr2.name is None
    assert google.calls == []  # never called again this run


def test_summary_reports_both_sources(monkeypatch):
    svc, crustdata, google = _service(monkeypatch)
    assert "crustdata summary" in svc.summary()
    assert "Google Search fallback" in svc.summary()
