"""
Shared test setup.

Every test that needs language understanding uses Google Gemini:

  live    (default when GEMINI_API_KEY is set)   the Gemini API is called
  replay  (no key, or TEST_LLM=replay)           answers Gemini really gave, recorded in
                                                 data/llm_recordings.json (python -m app record)

There is no hand-written fake model. Besides replay, only failures are simulated (a tool
timeout, a broken or compromised model response, a network error), because they cannot be
produced on demand from a live service. The mode is printed in the pytest header.
Force a mode with TEST_LLM=live or TEST_LLM=replay.
"""
import json
import os
from pathlib import Path

import pytest

from app.config import PROJECT_ROOT
from app.engine import Engine

DATA = Path(__file__).resolve().parent.parent / "data"
LLM_SETTINGS = ("GEMINI_API_KEY", "LLM_PROVIDER", "LLM_MODEL")


def load_request(name: str) -> dict:
    """The request part of a scenario in data/scenarios/, e.g. "allow/00_happy_path"."""
    from app.scenarios import request
    return request(name)


def directory_grants(eng, user_id):
    data = json.loads(eng.tools.directory_path.read_text(encoding="utf-8"))
    return next(u for u in data["users"] if u["user_id"] == user_id)["access"]


# ------------------------------------------------------------------ Gemini settings
def _env_file() -> dict:
    """Read .env without touching os.environ, so configuration tests are not affected."""
    path = PROJECT_ROOT / ".env"
    out = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k.strip().removeprefix("export ").strip()] = v.strip().strip("\"'")
    return out


def gemini_settings() -> dict:
    env = _env_file()
    return {k: os.environ.get(k) or env.get(k) for k in LLM_SETTINGS if os.environ.get(k) or env.get(k)}


def gemini_key_problem() -> str | None:
    """Why the Gemini key cannot be used, or None if it looks fine."""
    path = PROJECT_ROOT / ".env"
    key = gemini_settings().get("GEMINI_API_KEY", "")
    if not key:
        if not path.exists():
            return f"no .env file at {path} - create it from .env.example and add GEMINI_API_KEY"
        return ".env exists but has no GEMINI_API_KEY line"
    if key.startswith("paste-"):
        return ".env still contains the placeholder - paste your real key after GEMINI_API_KEY="
    return None


def has_gemini_key() -> bool:
    return gemini_key_problem() is None


def llm_test_mode() -> str:
    forced = os.environ.get("TEST_LLM", "").strip().lower()
    if forced in ("live", "replay"):
        return forced
    return "live" if has_gemini_key() else "replay"




def pytest_report_header(config):
    if llm_test_mode() == "live":
        return "LLM: live Gemini API"
    return (f"LLM: REPLAY of recorded Gemini answers (data/llm_recordings.json) - "
            f"{gemini_key_problem() or 'TEST_LLM=replay'}")


class SessionCachedGemini:
    """The Gemini client. An identical prompt is sent once per test run and its
    answer is reused, so repeated scenarios do not hit the API rate limit."""

    def __init__(self, client):
        self.client = client
        self.name = client.name
        self.model = client.model
        self.calls = 0
        self._answers: dict[tuple[str, str], str] = {}

    def answered(self, request_text: str) -> bool:
        """True if Gemini answered the prompt for this request text during this run
        (live, or a recorded answer in replay mode)."""
        from app.llm import build_user_prompt
        prompt = build_user_prompt(request_text)
        return any(user == prompt for _, user in self._answers)

    RATE_LIMIT_WAITS = (20, 40, 60)   # seconds to wait after HTTP 429 before trying again

    def complete(self, system: str, user: str) -> str:
        key = (system, user)
        if key not in self._answers:
            self.calls += 1
            self._answers[key] = self._call_with_rate_limit_wait(system, user)
        return self._answers[key]

    def _call_with_rate_limit_wait(self, system: str, user: str) -> str:
        """Many prompts in one run can exceed the API's requests-per-minute quota.
        A 429 is a quota signal, not a system failure, so the tests wait and try again."""
        import time
        from app.llm import LLMServiceError
        for wait in (*self.RATE_LIMIT_WAITS, None):
            try:
                return self.client.complete(system, user)
            except LLMServiceError as e:
                # 429 = quota; 503 / timeout = a temporary outage of the service, not of this system
                temporary = any(x in str(e) for x in ("429", "503", "connection failed"))
                if not temporary or wait is None:
                    raise
                time.sleep(wait)


class RecordedGemini(SessionCachedGemini):
    """Replay mode: answers Gemini gave earlier. A text that was never recorded skips the
    test (it is not a failure of the system) and says how to record it."""
    RATE_LIMIT_WAITS = ()

    def complete(self, system: str, user: str) -> str:
        import re
        m = re.search(r"<request>\n(.*?)\n</request>", user, re.DOTALL)
        if m and not self.client.has(m.group(1)):
            pytest.skip("no recorded Gemini answer for this text - run: python -m app record")
        return super().complete(system, user)


@pytest.fixture(scope="session")
def gemini():
    """One Gemini client for the whole test run: the live API, or recorded answers."""
    if llm_test_mode() == "replay":
        from app.llm import ReplayLLM
        return RecordedGemini(ReplayLLM())
    problem = gemini_key_problem()
    if problem:
        pytest.skip(f"Gemini key not usable: {problem}")
    from app.llm import GeminiLLM
    with pytest.MonkeyPatch.context() as mp:
        for k, v in gemini_settings().items():
            mp.setenv(k, v)
        client = GeminiLLM()
    return SessionCachedGemini(client)


@pytest.fixture(scope="session")
def live_gemini(gemini):
    """For the few tests that are about the Gemini API itself."""
    if not isinstance(gemini.client, __import__("app.llm", fromlist=["GeminiLLM"]).GeminiLLM):
        pytest.skip("needs the live Gemini API (set GEMINI_API_KEY)")
    return gemini


@pytest.fixture
def engine(tmp_path, gemini):
    """A fresh, isolated engine per test (its own directory copy, DB and audit log),
    connected to the Gemini API."""
    eng = Engine(runtime_dir=tmp_path, llm=gemini, sleep=lambda s: None)
    yield eng
    eng.store.close()


class NoLLMAllowed:
    """Used where the flow must stop BEFORE the model is called. Any call fails the test."""
    name = "none"

    def complete(self, system, user):
        raise AssertionError("the LLM must not be called in this test")


@pytest.fixture
def engine_without_llm(tmp_path):
    """An engine for tests that prove a check happens before any LLM call."""
    eng = Engine(runtime_dir=tmp_path, llm=NoLLMAllowed(), sleep=lambda s: None)
    yield eng
    eng.store.close()
