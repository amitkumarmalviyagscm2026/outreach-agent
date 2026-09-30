"""chat_with_browser_search(): the request carries the browser_search tool
(not response_format, which is unreliable combined with it on gpt-oss),
and cited URLs are extracted from the response's executed_tools field
regardless of its exact nesting."""
import httpx

import src.groq_client as mod
from src.groq_client import chat_with_browser_search


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

    def post(self, url, json, headers=None):
        self._posted_bodies.append(json)
        return FakeResponse(200, {
            "choices": [{
                "message": {
                    "content": '{"found": true, "name": "Priya"}',
                    "executed_tools": [
                        {
                            "type": "browser_search",
                            "output": {
                                "results": [
                                    {"url": "https://www.linkedin.com/in/priya", "title": "Priya Sharma"},
                                    {"url": "https://example.com/other"},
                                ]
                            },
                        }
                    ],
                }
            }]
        })

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass


def test_browser_search_sends_tool_not_response_format(monkeypatch):
    posted_bodies = []
    monkeypatch.setattr(mod.httpx, "Client", lambda timeout: FakeClient(posted_bodies))
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)

    chat_with_browser_search("find someone", "fake-key")

    assert len(posted_bodies) == 1
    body = posted_bodies[0]
    assert body["tools"] == [{"type": "browser_search"}]
    assert body["tool_choice"] == "required"
    assert "response_format" not in body


def test_browser_search_extracts_text_and_urls_regardless_of_nesting(monkeypatch):
    monkeypatch.setattr(mod.httpx, "Client", lambda timeout: FakeClient([]))
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)

    text, sources = chat_with_browser_search("find someone", "fake-key")

    assert text == '{"found": true, "name": "Priya"}'
    assert sources == ["https://www.linkedin.com/in/priya", "https://example.com/other"]


def test_extract_urls_handles_missing_executed_tools():
    assert mod._extract_urls(None) == []
    assert mod._extract_urls([]) == []
