"""The .env loader: reads KEY=VALUE, never overrides values already set in the shell."""
import os

from app.config import load_env


def test_env_file_is_loaded(tmp_path, monkeypatch):
    monkeypatch.delenv("DEMO_KEY", raising=False)
    monkeypatch.delenv("DEMO_MODEL", raising=False)
    f = tmp_path / ".env"
    f.write_text('# comment\n\nDEMO_KEY="abc123"\nexport DEMO_MODEL=gemini-2.5-flash-lite\nnot a pair\n')
    assert sorted(load_env(f)) == ["DEMO_KEY", "DEMO_MODEL"]
    assert os.environ["DEMO_KEY"] == "abc123"
    assert os.environ["DEMO_MODEL"] == "gemini-2.5-flash-lite"


def test_terminal_value_wins_over_env_file(tmp_path, monkeypatch):
    monkeypatch.setenv("DEMO_KEY", "from-terminal")
    f = tmp_path / ".env"
    f.write_text("DEMO_KEY=from-file\n")
    assert load_env(f) == []
    assert os.environ["DEMO_KEY"] == "from-terminal"


def test_missing_env_file_is_fine(tmp_path):
    assert load_env(tmp_path / "nope.env") == []
