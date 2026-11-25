"""Gemini call hygiene: key in header (not URL), no error detail leaked to clients."""
import io
import json
import os
import sys
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from ai import provider  # noqa: E402

RING = {"ring_id": "R01", "aggregate": 0.9, "expected_loss": 1000, "closure_type": "transaction",
        "scores": {"value": 0.9}, "abstained": [], "evidence": {}}


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
