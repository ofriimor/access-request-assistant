"""
Gemini provider tests: configuration, calls to the API, and simulated outages
(network down, HTTP 503, empty response) that cannot be produced on demand.
"""
import urllib.error

import pytest

from app.engine import Engine
from app.llm import LLMServiceError, get_llm, _post_json
from tests.conftest import gemini_settings, load_request


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


def test_gemini_is_selected(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-placeholder")
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    assert get_llm().name == "gemini"


def test_provider_network_error_is_safe(monkeypatch):
    """Failure simulation: the network is down."""
    def fail(*args, **kwargs):
        raise urllib.error.URLError('connection refused')
    monkeypatch.setattr('app.llm.urllib.request.urlopen', fail)
    monkeypatch.setattr('app.llm.time.sleep', lambda s: None)
    with pytest.raises(LLMServiceError, match='connection failed'):
        _post_json('https://example.invalid', {}, {})


def test_engine_stops_before_action_when_llm_service_fails(engine_without_llm):
    class GeminiOutage:
        """Failure simulation: the Gemini service is down (HTTP 503)."""
        name = 'gemini-outage-simulation'
        def complete(self, system, user):
            raise LLMServiceError('LLM API HTTP 503')
    eng = engine_without_llm
    eng.llm = GeminiOutage()
    state = eng.submit(load_request('allow/00_happy_path'))
    assert state['status'] == 'FAILED_SAFE'
    assert 'plan' not in state
    assert state['error'] == 'LLM API HTTP 503'
    assert 'not available' in state['model_problem']          # the user is told plainly
    assert eng.tools.directory_path.exists()


def test_explicit_gemini_requires_key(monkeypatch):
    monkeypatch.setenv('LLM_PROVIDER', 'gemini')
    monkeypatch.delenv('GEMINI_API_KEY', raising=False)
    with pytest.raises(ValueError, match='GEMINI_API_KEY'):
        get_llm()


def test_gemini_returns_valid_understanding(gemini):
    """A call to Gemini returns JSON that passes the Understanding schema."""
    from app.llm import SYSTEM_PROMPT, build_user_prompt, parse_understanding
    raw = gemini.client.complete(SYSTEM_PROMPT, build_user_prompt(
        "I need read access to the HR portal for 14 days because I review onboarding forms"))
    und = parse_understanding(raw)
    assert und.intent == "grant_access"
    assert und.system == "hr_portal"
    assert und.access_level == "read"
    assert und.duration_days == 14


def test_gemini_empty_response_fails_safe(monkeypatch):
    """Failure simulation: Gemini answers with no candidates."""
    from app.llm import GeminiLLM
    monkeypatch.setenv('GEMINI_API_KEY', 'placeholder')
    monkeypatch.setattr('app.llm._post_json', lambda *args: {'candidates': []})
    with pytest.raises(LLMServiceError, match='no usable response'):
        GeminiLLM().complete('system', 'user')


def test_extracts_structured_fields(engine):
    state = engine.submit(load_request("allow/00_happy_path"))
    und = state["understanding"]
    assert state["llm_provider"] == "gemini"
    assert und["intent"] == "grant_access"
    assert und["system"] == "crm"
    assert und["access_level"] == "write"
    assert und["duration_days"] == 90
    assert und["justification"]


def test_unavailable_model_fails_safe(tmp_path, monkeypatch, gemini):
    # A call to Gemini with a model that does not exist: the API answers 404.
    for k, v in gemini_settings().items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("LLM_MODEL", "gemini-model-that-does-not-exist")
    from app.llm import GeminiLLM
    eng = Engine(runtime_dir=tmp_path, llm=GeminiLLM())
    state = eng.submit(load_request("allow/00_happy_path"))
    assert state["status"] == "FAILED_SAFE"
    assert "404" in state["error"]
    assert not any(e["event"] == "tool_call" and e["data"].get("tool") == "grant_access"
                   for e in eng.audit.read())
    eng.store.close()


def test_rate_limit_is_retried_patiently(monkeypatch):
    """Failure simulation: Gemini answers 429 (quota per minute) twice, then succeeds."""
    import io
    import urllib.error
    calls, waits = [], []

    def fake_urlopen(request, timeout):
        calls.append(1)
        if len(calls) <= 2:
            raise urllib.error.HTTPError(request.full_url, 429, "Too Many Requests", {}, None)
        return io.BytesIO(b'{"ok": true}')

    monkeypatch.setattr("app.llm.urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("app.llm.time.sleep", waits.append)
    assert _post_json("https://example.invalid", {}, {}) == {"ok": True}
    assert len(calls) == 3 and waits[0] >= 10 and waits[1] >= 20



def test_timeout_is_retried_then_succeeds(monkeypatch):
    """Failure simulation: the first call to Gemini times out, the second answers."""
    import io
    calls = []

    def fake_urlopen(request, timeout):
        calls.append(1)
        if len(calls) == 1:
            raise TimeoutError("read timed out")
        return io.BytesIO(b'{"ok": true}')

    monkeypatch.setattr("app.llm.urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("app.llm.time.sleep", lambda s: None)
    assert _post_json("https://example.invalid", {}, {}) == {"ok": True}
    assert len(calls) == 2


def test_request_stopped_by_outage_can_be_resubmitted(engine, gemini):
    """Gemini was down, so the request stopped. When Gemini is back, submitting the same
    request runs it again (its ID comes from its content, so it used to stay stuck)."""
    class GeminiOutage:
        name = "gemini-outage-simulation"
        def complete(self, system, user):
            raise LLMServiceError("LLM API HTTP 503")

    raw = load_request("allow/00_happy_path")
    engine.llm = GeminiOutage()
    first = engine.submit(raw)
    assert first["status"] == "FAILED_SAFE" and first["model_problem"]

    engine.llm = gemini                          # Gemini is back
    second = engine.submit(raw)
    assert second["request_id"] == first["request_id"]
    assert second["status"] == "AWAITING_APPROVAL" and not second.get("replayed")
    assert len(engine.store.all()) == 1
