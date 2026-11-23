"""
Scenarios = the sample requests of the project, each with the outcome it must reach.

    data/scenarios/allow/        -> AWAITING_APPROVAL (and COMPLETED after approval)
    data/scenarios/needs_info/   -> NEEDS_INFO
    data/scenarios/blocked/      -> BLOCKED
    data/scenarios/invalid/      -> rejected at intake

One folder serves the demo, the CLI and the automated tests.
A scenario is referred to by its name, e.g. "allow/00_happy_path".
"""
from __future__ import annotations

import json
from pathlib import Path

SCENARIOS_DIR = Path(__file__).resolve().parent.parent / "data" / "scenarios"
GROUPS = ("allow", "needs_info", "blocked", "invalid")


def all_scenarios() -> list[Path]:
    return sorted(SCENARIOS_DIR.glob("*/*.json"))


def name_of(path: Path) -> str:
    return f"{path.parent.name}/{path.stem}"


def resolve(ref: str | Path) -> Path:
    """Accept a scenario name ("allow/00_happy_path"), a unique short name ("00_happy_path")
    or a path to a JSON file."""
    p = Path(ref)
    if p.suffix == ".json" and p.exists():
        return p
    ref = str(ref).removesuffix(".json").replace("\\", "/")
    exact = SCENARIOS_DIR / f"{ref}.json"
    if exact.exists():
        return exact
    matches = [s for s in all_scenarios() if s.stem == ref or name_of(s).endswith(ref)]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise FileNotFoundError(f"no scenario named '{ref}' - run 'python -m app scenario' to list them")
    raise FileNotFoundError(f"'{ref}' matches several scenarios: {', '.join(name_of(m) for m in matches)}")


def load(ref: str | Path) -> dict:
    """The full scenario: description, request and expected outcome."""
    data = json.loads(resolve(ref).read_text(encoding="utf-8"))
    if "request" not in data:          # a plain request file: no description or expectation
        data = {"description": "", "request": data, "expect": {}}
    return data


def request(ref: str | Path) -> dict:
    """Only the request part, as the engine receives it."""
    return load(ref)["request"]
