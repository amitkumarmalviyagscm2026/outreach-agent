"""generate_grounded_text(): the request carries the google_search tool
(not response_schema, which Gemini rejects alongside it), and grounding
citations are correctly pulled out of the response."""
import httpx

import src.gemini_client as mod
from src.gemini_client import generate_grounded_text


class FakeResponse:
    def __init__(self, status_code, json_data):
        self.status_code = status_code
        self._json_data = json_data
        self.reason_phrase = "OK"
        self.request = httpx.Request("POST", "https://example.com")

    def json(self):
        return self._json_data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=self.request, response=self)


class FakeClient:
    def __init__(self, posted_bodies):
        self._posted_bodies = posted_bodies

    def post(self, url, json):
        self._posted_bodies.append(json)
        return FakeResponse(200, {
            "candidates": [{
                "content": {"parts": [{"text": '{"found": true, "name": "Priya"}'}]},
                "groundingMetadata": {
                    "groundingChunks": [
                        {"web": {"uri": "https://www.linkedin.com/in/priya", "title": "Priya Sharma"}},
                        {"web": {"uri": "https://example.com/other"}},
                    ]
                },
            }]
        })

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass


def test_grounded_call_sends_google_search_tool_not_response_schema(monkeypatch):
    posted_bodies = []
    monkeypatch.setattr(mod.httpx, "Client", lambda timeout: FakeClient(posted_bodies))
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)

    generate_grounded_text(["fake-model"], "find someone", "fake-key")

    assert len(posted_bodies) == 1
    body = posted_bodies[0]
    assert body["tools"] == [{"google_search": {}}]
    assert "response_mime_type" not in body.get("generationConfig", {})
    assert "response_schema" not in body.get("generationConfig", {})


def test_grounded_call_extracts_text_and_sources(monkeypatch):
    monkeypatch.setattr(mod.httpx, "Client", lambda timeout: FakeClient([]))
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)

    text, sources = generate_grounded_text(["fake-model"], "find someone", "fake-key")

    assert text == '{"found": true, "name": "Priya"}'
    assert sources == [
        {"uri": "https://www.linkedin.com/in/priya", "title": "Priya Sharma"},
        {"uri": "https://example.com/other"},
    ]
