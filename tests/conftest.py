"""
Shared test setup.

Every test that needs language understanding talks to the real Google Gemini API.
There is no fake model: the only simulated parts are failures (a tool timeout, a broken
or compromised model response, a network error), which cannot be produced on demand
from a real service.

The tests need GEMINI_API_KEY in .env (or in the shell) and use API quota.
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
    return json.loads((DATA / "requests" / name).read_text(encoding="utf-8"))


def directory_grants(eng, user_id):
    data = json.loads(eng.tools.directory_path.read_text(encoding="utf-8"))
    return next(u for u in data["users"] if u["user_id"] == user_id)["access"]


# ------------------------------------------------------------------ real Gemini settings
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
    """Why the real Gemini key cannot be used, or None if it looks fine."""
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


class SessionCachedGemini:
    """The real Gemini client. An identical prompt is sent once per test run and its real
    answer is reused, so repeated scenarios do not hit the API rate limit."""

    def __init__(self, client):
        self.client = client
        self.name = client.name
        self.model = client.model
        self.calls = 0
        self._answers: dict[tuple[str, str], str] = {}

    def complete(self, system: str, user: str) -> str:
        key = (system, user)
        if key not in self._answers:
            self.calls += 1
            self._answers[key] = self.client.complete(system, user)
        return self._answers[key]


@pytest.fixture(scope="session")
def gemini():
    """One real Gemini client for the whole test run."""
    problem = gemini_key_problem()
    if problem:
        pytest.skip(f"Gemini key not usable: {problem}")
    from app.llm import GeminiLLM
    with pytest.MonkeyPatch.context() as mp:
        for k, v in gemini_settings().items():
            mp.setenv(k, v)
        client = GeminiLLM()
    return SessionCachedGemini(client)


@pytest.fixture
def engine(tmp_path, gemini):
    """A fresh, isolated engine per test (its own directory copy, DB and audit log),
    connected to the real Gemini API."""
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
