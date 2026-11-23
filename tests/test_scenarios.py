"""
Scenario suite: every JSON file in data/scenarios/ is a real prompt sent to the real
Gemini API, with the outcome the system must reach.

    data/scenarios/allow/       -> AWAITING_APPROVAL, then approve + execute -> COMPLETED
    data/scenarios/needs_info/  -> NEEDS_INFO, no plan
    data/scenarios/blocked/     -> BLOCKED, with the expected reason
    data/scenarios/invalid/     -> rejected at intake, the model is never called

To add a case, drop a new JSON file into the right folder - no code changes needed.
Run all scenarios:   python -m pytest tests/test_scenarios.py -v
Run one scenario:    python -m pytest tests/test_scenarios.py -v -k hebrew
"""
import json
from pathlib import Path

import pytest

from app.engine import IntakeValidationError
from tests.conftest import directory_grants

from app.scenarios import all_scenarios

CASES = all_scenarios()


def _id(path: Path) -> str:
    return f"{path.parent.name}/{path.stem}"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _check(condition: bool, case: dict, what: str, state: dict) -> None:
    if not condition:
        detail = {k: state.get(k) for k in ("status", "understanding", "decision") if k in state}
        pytest.fail(f"{case['description']}\n  expected: {what}\n  got: "
                    f"{json.dumps(detail, ensure_ascii=False, indent=2)}", pytrace=False)


@pytest.mark.parametrize("path", [p for p in CASES if p.parent.name == "invalid"], ids=_id)
def test_invalid_input(path, engine_without_llm):
    case = _load(path)
    with pytest.raises(IntakeValidationError) as exc:
        engine_without_llm.submit(case["request"])
    assert case["expect"]["error_contains"] in " ".join(exc.value.errors)
    assert engine_without_llm.store.all() == []


@pytest.mark.parametrize("path", [p for p in CASES if p.parent.name != "invalid"], ids=_id)
def test_scenario(path, engine, gemini):
    case = _load(path)
    exp = case["expect"]
    state = engine.submit(case["request"])

    # The request really went through Gemini.
    _check(state.get("llm_provider") == "gemini" and "understanding" in state,
           case, "the request was understood by the real Gemini model", state)
    assert gemini.answered(case["request"]["request_text"]), "this prompt was not answered by Gemini"

    und = state["understanding"]
    decision = state.get("decision") or {}

    _check(state["status"] == exp["status"], case, f"status {exp['status']}", state)
    for field in ("system", "access_level", "intent"):
        if field in exp:
            _check(und[field] == exp[field], case, f"{field} = {exp[field]!r}", state)
    if "duration_days" in exp:
        _check(decision.get("duration_days") == exp["duration_days"], case,
               f"duration {exp['duration_days']} days", state)
    if "reason_contains" in exp:
        _check(any(exp["reason_contains"] in r for r in decision.get("block_reasons", [])),
               case, f"a block reason containing {exp['reason_contains']!r}", state)
    if "missing_contains" in exp:
        _check(any(exp["missing_contains"] in m for m in decision.get("missing", [])),
               case, f"a missing item containing {exp['missing_contains']!r}", state)
    if "assumption_contains" in exp:
        _check(any(exp["assumption_contains"] in a for a in decision.get("assumptions", [])),
               case, f"an assumption containing {exp['assumption_contains']!r}", state)
    if "approver" in exp:
        _check(state.get("approver_required") == exp["approver"], case,
               f"approver {exp['approver']}", state)

    if exp["status"] != "AWAITING_APPROVAL":
        assert "plan" not in state                     # nothing can be approved or executed
        return

    # Allowed requests go all the way: manager approves, grant is written and verified.
    target = case["request"]["target_user_id"]
    assert directory_grants(engine, target) == []
    engine.decide(state["request_id"], state["approver_required"], approve=True)
    final = engine.execute(state["request_id"])
    _check(final["status"] == "COMPLETED" and final["verification"]["ok"], case,
           "COMPLETED with a verified grant", final)
    grants = directory_grants(engine, target)
    assert len(grants) == 1
    assert grants[0]["system"] == und["system"] and grants[0]["access_level"] == und["access_level"]
