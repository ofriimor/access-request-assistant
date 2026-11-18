"""
Loads local settings from a `.env` file in the project root.

The file holds secrets (GEMINI_API_KEY) and is listed in .gitignore, so it never
reaches GitHub. `.env.example` documents which keys are expected.

Rules:
  - KEY=VALUE per line; blank lines and lines starting with # are ignored
  - surrounding quotes around VALUE are removed
  - a variable already set in the environment is NOT overridden
    (so a value typed in the terminal always wins over the file)

Called only from the entry points (CLI and demo), never from the tests.
"""
from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def load_env(path: Path | None = None) -> list[str]:
    """Load KEY=VALUE pairs into os.environ. Returns the names that were set."""
    path = path or PROJECT_ROOT / ".env"
    if not path.exists():
        return []
    loaded = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value
            loaded.append(key)
    return loaded
