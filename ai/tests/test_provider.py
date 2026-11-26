"""Gemini call hygiene: key in header (not URL), no error detail leaked to clients."""
import io
import json
import os
import sys
import urllib.error

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from ai import provider  # noqa: E402

RING = {"ring_id": "R01", "aggregate": 0.9, "expected_loss": 1000, "closure_type": "transaction",
        "scores": {"value": 0.9}, "abstained": [], "evidence": {}}


@pytest.fixture(autouse=True)
def no_real_keys(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_key_in_header_not_url(monkeypatch):
    seen = {}

    def fake_urlopen(req, timeout=0):
        seen["url"], seen["headers"] = req.full_url, dict(req.header_items())
        seen["body"] = json.loads(req.data)
        return _Resp(json.dumps({"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}).encode())

    monkeypatch.setattr(provider.urllib.request, "urlopen", fake_urlopen)
    assert provider.call_gemini_api("hi", "SECRETKEY", system="be brief") == "ok"
    assert "SECRETKEY" not in seen["url"]
    assert seen["headers"]["X-goog-api-key"] == "SECRETKEY"
    assert seen["body"]["systemInstruction"]["parts"][0]["text"] == "be brief"


def test_llm_failure_does_not_leak_details(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "SECRETKEY")

    def boom(req, timeout=0):
        raise urllib.error.URLError("https://x/?key=SECRETKEY exploded")

    monkeypatch.setattr(provider.urllib.request, "urlopen", boom)
    res = provider.generate_narrative(RING)
    assert res["status"] == "llm_error_fallback"
    assert res["error"] == "llm_unavailable"
    assert "SECRETKEY" not in json.dumps(res)
    assert res["narrative"]  # deterministic fallback still delivered


def test_groq_request_shape(monkeypatch):
    seen = {}

    def fake_urlopen(req, timeout=0):
        seen["url"], seen["headers"] = req.full_url, dict(req.header_items())
        seen["body"] = json.loads(req.data)
        return _Resp(json.dumps({"choices": [{"message": {"content": " ok "}}]}).encode())

    monkeypatch.delenv("GROQ_MODEL", raising=False)
    monkeypatch.setattr(provider.urllib.request, "urlopen", fake_urlopen)
    assert provider.call_groq_api("hi", "SECRETKEY", system="be brief") == "ok"
    assert "SECRETKEY" not in seen["url"]
    assert seen["headers"]["Authorization"] == "Bearer SECRETKEY"
    assert seen["headers"]["User-agent"] == "circe/2"
    assert seen["body"]["model"] == "openai/gpt-oss-120b"
    assert seen["body"]["messages"][0] == {"role": "system", "content": "be brief"}


@pytest.mark.parametrize("gemini", ["raises", "empty", "rejected"])
def test_groq_used_when_gemini_unusable(monkeypatch, gemini):
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    monkeypatch.setenv("GROQ_API_KEY", "q")

    def first(*a, **k):
        if gemini == "raises":
            raise urllib.error.URLError("down")
        return "" if gemini == "empty" else "bad"

    monkeypatch.setattr(provider, "call_gemini_api", first)
    monkeypatch.setattr(provider, "call_groq_api", lambda *a, **k: "from groq")
    assert provider.call_llm("p", ok=lambda t: t != "bad") == "from groq"


def test_groq_alone_and_not_called_when_gemini_works(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "q")
    monkeypatch.setattr(provider, "call_groq_api", lambda *a, **k: "from groq")
    assert provider.generate_narrative(RING)["narrative"] == "from groq"
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    monkeypatch.setattr(provider, "call_gemini_api", lambda *a, **k: "from gemini")
    assert provider.call_llm("p") == "from gemini"


def test_narrative_prompt_is_redacted(monkeypatch):
    from pipeline.service import entities_map
    eid, ent = next(iter(entities_map().items()))
    seen = {}
    monkeypatch.setenv("GROQ_API_KEY", "q")
    monkeypatch.setattr(provider, "call_groq_api", lambda p, k, system="": seen.setdefault("p", p))
    provider.generate_narrative({**RING, "entities": [eid], "evidence": {"x": f"{ent['name']} PAN ABCDE1234F"}})
    assert ent["name"] not in seen["p"] and "ABCDE1234F" not in seen["p"]
