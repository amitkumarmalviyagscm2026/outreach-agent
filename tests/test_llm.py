"""Gemini -> Groq fallback routing, with both providers faked."""
import pytest

from src import gemini_client, groq_client, llm
from src.llm import LLMKeys

SCHEMA = {"type": "OBJECT", "properties": {"a": {"type": "STRING"}}, "required": ["a"]}


@pytest.fixture(autouse=True)
def reset_state():
    llm._reset_state_for_tests()
    yield
    llm._reset_state_for_tests()


def _recorder(result=None, error=None):
    calls = []

    def fake(*args, **kwargs):
        calls.append(kwargs)
        if error:
            raise RuntimeError(error)
        return result

    return fake, calls


def test_gemini_success_never_touches_groq(monkeypatch):
    gem, gem_calls = _recorder(result={"a": "gemini"})
    groq, groq_calls = _recorder(result={"a": "groq"})
    monkeypatch.setattr(gemini_client, "generate_json", gem)
    monkeypatch.setattr(groq_client, "generate_json", groq)

    out = llm.generate_json(LLMKeys("g", "q"), "p", SCHEMA)
    assert out == {"a": "gemini"}
    assert len(gem_calls) == 1 and groq_calls == []


def test_gemini_failure_falls_back_to_groq(monkeypatch):
    gem, _ = _recorder(error="503 on all models")
    groq, groq_calls = _recorder(result={"a": "groq"})
    monkeypatch.setattr(gemini_client, "generate_json", gem)
    monkeypatch.setattr(groq_client, "generate_json", groq)

    out = llm.generate_json(LLMKeys("g", "q"), "p", SCHEMA)
    assert out == {"a": "groq"}
    assert len(groq_calls) == 1
    assert llm.stats.groq_success == 1 and llm.stats.gemini_failures == 1


def test_with_groq_configured_gemini_fails_fast(monkeypatch):
    gem, gem_calls = _recorder(result={"a": "gemini"})
    monkeypatch.setattr(gemini_client, "generate_json", gem)

    llm.generate_json(LLMKeys("g", "q"), "p", SCHEMA)
    assert gem_calls[0]["retries"] == llm.FAST_FAIL_RETRIES


def test_without_groq_gemini_keeps_full_retries_and_errors_propagate(monkeypatch):
    gem, gem_calls = _recorder(error="503 on all models")
    monkeypatch.setattr(gemini_client, "generate_json", gem)

    with pytest.raises(RuntimeError, match="503"):
        llm.generate_json(LLMKeys("g", None), "p", SCHEMA)
    assert gem_calls[0]["retries"] == gemini_client.RETRIES_PER_MODEL


def test_after_gemini_fails_it_is_skipped_during_cooldown(monkeypatch):
    gem, gem_calls = _recorder(error="503 on all models")
    groq, groq_calls = _recorder(result={"a": "groq"})
    monkeypatch.setattr(gemini_client, "generate_json", gem)
    monkeypatch.setattr(groq_client, "generate_json", groq)

    keys = LLMKeys("g", "q")
    llm.generate_json(keys, "p1", SCHEMA)  # Gemini fails -> cooldown starts
    llm.generate_json(keys, "p2", SCHEMA)
    llm.generate_json(keys, "p3", SCHEMA)

    assert len(gem_calls) == 1  # not retried on p2/p3
    assert len(groq_calls) == 3


def test_groq_failure_during_cooldown_retries_gemini(monkeypatch):
    llm._gemini_cooldown_until = float("inf")
    gem, gem_calls = _recorder(result={"a": "gemini"})
    groq, _ = _recorder(error="groq down")
    monkeypatch.setattr(gemini_client, "generate_json", gem)
    monkeypatch.setattr(groq_client, "generate_json", groq)

    out = llm.generate_json(LLMKeys("g", "q"), "p", SCHEMA)
    assert out == {"a": "gemini"}
    assert len(gem_calls) == 1
    assert llm._gemini_cooldown_until == 0.0  # Gemini recovered -> cooldown cleared


def test_both_down_raises_with_both_reasons(monkeypatch):
    gem, _ = _recorder(error="gemini 503")
    groq, _ = _recorder(error="groq 429")
    monkeypatch.setattr(gemini_client, "generate_json", gem)
    monkeypatch.setattr(groq_client, "generate_json", groq)

    with pytest.raises(RuntimeError) as exc:
        llm.generate_json(LLMKeys("g", "q"), "p", SCHEMA)
    assert "gemini 503" in str(exc.value) and "groq 429" in str(exc.value)


def test_schema_conversion_for_groq_strict_mode():
    gemini_style = {
        "type": "OBJECT",
        "properties": {
            "companies": {
                "type": "ARRAY",
                "items": {
                    "type": "OBJECT",
                    "properties": {"name": {"type": "STRING", "description": "d"}},
                    "required": ["name"],
                },
            }
        },
        "required": ["companies"],
    }
    out = groq_client.to_strict_json_schema(gemini_style)
    assert out["type"] == "object"
    assert out["additionalProperties"] is False
    item = out["properties"]["companies"]["items"]
    assert item["type"] == "object"
    assert item["additionalProperties"] is False
    assert item["required"] == ["name"]
    assert item["properties"]["name"] == {"type": "string", "description": "d"}
