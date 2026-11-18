import json
from pathlib import Path

import pytest

from app.engine import Engine
from tests.fake_llm import FakeLLM

DATA = Path(__file__).resolve().parent.parent / "data"


def load_request(name: str) -> dict:
    return json.loads((DATA / "requests" / name).read_text(encoding="utf-8"))


@pytest.fixture
def engine(tmp_path):
    """A fresh, isolated engine per test: its own directory copy, DB and audit log."""
    eng = Engine(runtime_dir=tmp_path, llm=FakeLLM(), sleep=lambda s: None)
    yield eng
    eng.store.close()


def directory_grants(eng, user_id):
    data = json.loads(eng.tools.directory_path.read_text(encoding="utf-8"))
    return next(u for u in data["users"] if u["user_id"] == user_id)["access"]
