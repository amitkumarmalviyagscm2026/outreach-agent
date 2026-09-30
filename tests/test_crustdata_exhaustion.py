"""Best-effort detection of an exhausted Crustdata credit balance, and
that CrustdataClient actually raises/propagates it instead of retrying
forever or being silently swallowed by _find()'s broad except clause."""
import httpx
import pytest

from src.crustdata_client import CrustdataClient, CrustdataExhausted, _looks_like_exhausted_balance


def _response(status_code, text=""):
    request = httpx.Request("POST", "https://api.crustdata.com/person/search")
    return httpx.Response(status_code, text=text, request=request)


def test_402_is_always_exhausted():
    assert _looks_like_exhausted_balance(_response(402)) is True


def test_400_with_credit_keyword_is_exhausted():
    assert _looks_like_exhausted_balance(_response(400, '{"error": "insufficient credits"}')) is True


def test_403_with_balance_keyword_is_exhausted():
    assert _looks_like_exhausted_balance(_response(403, "account balance too low")) is True


def test_ordinary_400_is_not_exhausted():
    assert _looks_like_exhausted_balance(_response(400, '{"error": "invalid filter field"}')) is False


def test_ordinary_500_is_not_exhausted():
    assert _looks_like_exhausted_balance(_response(500, "internal server error")) is False


def test_exhaustion_propagates_through_find_not_swallowed(monkeypatch):
    """_find()'s except RuntimeError would normally swallow a failure and
    return a blank Contact -- CrustdataExhausted must NOT take that path,
    since contact_search.py needs to see it to switch to the fallback."""
    client = CrustdataClient("fake-key")

    def raise_exhausted(company_name, keywords):
        raise CrustdataExhausted("balance exhausted")

    monkeypatch.setattr(client, "_search_people", raise_exhausted)

    with pytest.raises(CrustdataExhausted):
        client._find("hr_ta", "Cipla", ["HR"], [])

    with pytest.raises(CrustdataExhausted):
        client.get_two_contacts("Cipla")
