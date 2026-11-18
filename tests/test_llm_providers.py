"""Real-provider configuration and safe failure tests; no network or API keys needed."""
import json
import urllib.error

import pytest

from app.engine import Engine
from app.llm import LLMServiceError, get_llm, _post_json
from tests.conftest import load_request


def test_missing_gemini_key_fails(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    with pytest.raises(ValueError, match="GEMINI_API_KEY"):
        get_llm()


def test_other_providers_rejected(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-placeholder")
    for provider in ("openai", "anthropic", "mock", "auto"):
        monkeypatch.setenv("LLM_PROVIDER", provider)
        with pytest.raises(ValueError, match="Only LLM_PROVIDER=gemini"):
            get_llm()


def test_real_gemini_selected(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-placeholder")
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    assert get_llm().name == "gemini"


def test_provider_network_error_is_safe(monkeypatch):
    def fail(*args, **kwargs):
        raise urllib.error.URLError('connection refused')
    monkeypatch.setattr('app.llm.urllib.request.urlopen', fail)
    with pytest.raises(LLMServiceError, match='connection failed'):
        _post_json('https://example.invalid', {}, {})


def test_engine_stops_before_action_when_llm_service_fails(engine):
    class BrokenLLM:
        name = 'broken-test-provider'
        def complete(self, system, user):
            raise LLMServiceError('LLM API HTTP 503')
    engine.llm = BrokenLLM()
    state = engine.submit(load_request('01_happy_path.json'))
    assert state['status'] == 'FAILED_SAFE'
    assert 'plan' not in state
    assert state['error'] == 'LLM API HTTP 503'
    assert engine.tools.directory_path.exists()


def test_explicit_gemini_requires_key(monkeypatch):
    monkeypatch.setenv('LLM_PROVIDER', 'gemini')
    monkeypatch.delenv('GEMINI_API_KEY', raising=False)
    with pytest.raises(ValueError, match='GEMINI_API_KEY'):
        get_llm()


def test_gemini_response_parsing(monkeypatch):
    from app.llm import GeminiLLM
    monkeypatch.setenv('GEMINI_API_KEY', 'placeholder')
    monkeypatch.setenv('LLM_MODEL', 'gemini-2.5-flash')
    def fake_post(url, headers, body):
        assert 'gemini-2.5-flash:generateContent' in url
        assert headers['x-goog-api-key'] == 'placeholder'
        assert body['generationConfig']['responseMimeType'] == 'application/json'
        return {'candidates': [{'content': {'parts': [{'text': '{"intent":"other"}'}]}}]}
    monkeypatch.setattr('app.llm._post_json', fake_post)
    assert GeminiLLM().complete('system', 'user') == '{"intent":"other"}'


def test_gemini_empty_response_fails_safe(monkeypatch):
    from app.llm import GeminiLLM
    monkeypatch.setenv('GEMINI_API_KEY', 'placeholder')
    monkeypatch.setattr('app.llm._post_json', lambda *args: {'candidates': []})
    with pytest.raises(LLMServiceError, match='no usable response'):
        GeminiLLM().complete('system', 'user')
