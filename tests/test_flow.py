"""
Automated tests for the mandatory scenarios, running against the real Gemini API.
Only failures are simulated (tool timeouts, corrupt tool data, a compromised model).
Run:  python -m pytest -v
"""
import json

import pytest

from app import engine as eng_mod
from app.engine import (ApprovalError, ApprovalRequired, IntakeValidationError,
                        ToolSchemaError)
from tests.conftest import directory_grants, load_request


# ---------------------------------------------------------------- 1. validation
def test_unknown_fields_are_rejected(engine_without_llm):
    raw = load_request("allow/00_happy_path") | {"auto_approve": True}
    with pytest.raises(IntakeValidationError):
        engine_without_llm.submit(raw)


# ---------------------------------------------------------------- 2. knowledge match
def test_expected_policy_is_top_knowledge_match(engine):
    state = engine.submit(load_request("allow/00_happy_path"))
    assert state["knowledge"][0]["policy_id"] == "POL-CRM-01"
    assert len(state["knowledge"]) >= 1
    assert "POL-CRM-01" in state["plan"]["policy_refs"]


def test_prod_db_request_matches_prod_policy(engine):
    state = engine.submit(load_request("blocked/01_admin_production_db"))
    assert state["knowledge"][0]["policy_id"] == "POL-PROD-03"


# ---------------------------------------------------------------- happy path
def test_happy_path_end_to_end(engine):
    state = engine.submit(load_request("allow/00_happy_path"))
    assert state["status"] == "AWAITING_APPROVAL"
    assert directory_grants(engine, "u1002") == []            # nothing happened yet

    engine.decide(state["request_id"], "u1001", approve=True)
    final = engine.execute(state["request_id"])
    assert final["status"] == "COMPLETED"
    assert final["verification"]["ok"] is True
    grants = directory_grants(engine, "u1002")
    assert len(grants) == 1 and grants[0]["system"] == "crm" and grants[0]["access_level"] == "write"


# ---------------------------------------------------------------- 3. approval gate
def test_execute_without_approval_is_blocked(engine):
    state = engine.submit(load_request("allow/00_happy_path"))
    with pytest.raises(ApprovalRequired):
        engine.execute(state["request_id"])
    assert directory_grants(engine, "u1002") == []
    assert any(e["event"] == "execute_blocked" for e in engine.audit.read(state["request_id"]))


def test_rejected_request_cannot_be_executed(engine):
    state = engine.submit(load_request("allow/10_support_crm_write_escalations"))
    engine.decide(state["request_id"], "u1012", approve=False, reason="use read-only")
    with pytest.raises(ApprovalRequired):
        engine.execute(state["request_id"])
    assert directory_grants(engine, "u1007") == []


def test_only_the_direct_manager_can_approve(engine):
    state = engine.submit(load_request("allow/00_happy_path"))
    with pytest.raises(ApprovalError):
        engine.decide(state["request_id"], "u1002", approve=True)   # self-approval
    with pytest.raises(ApprovalError):
        engine.decide(state["request_id"], "u1010", approve=True)   # wrong manager


def test_plan_changed_after_approval_is_blocked(engine):
    state = engine.submit(load_request("allow/00_happy_path"))
    rid = state["request_id"]
    engine.decide(rid, "u1001", approve=True)
    tampered = engine.store.get(rid)
    tampered["plan"]["params"]["duration_days"] = 180               # someone edits the plan
    engine.store.save(tampered)
    with pytest.raises(ApprovalRequired, match="plan changed"):
        engine.execute(rid)
    assert directory_grants(engine, "u1002") == []


# ---------------------------------------------------------------- 4. tool failure
def test_tool_timeout_retries_then_fails_safe(engine):
    state = engine.submit(load_request("allow/11_manager_requests_for_employee"))
    rid = state["request_id"]
    engine.decide(rid, "u1001", approve=True)
    failed = engine.execute(rid, simulate_failure="timeout")
    assert failed["status"] == "FAILED_SAFE"
    assert directory_grants(engine, "u1009") == []
    errors = [e for e in engine.audit.read(rid) if e["event"] == "tool_error"]
    assert len(errors) == engine.MAX_RETRIES                       # bounded retry

    recovered = engine.execute(rid)                                # after the outage
    assert recovered["status"] == "COMPLETED"
    assert len(directory_grants(engine, "u1009")) == 1


def test_flaky_tool_recovers_with_retry(engine):
    state = engine.submit(load_request("allow/11_manager_requests_for_employee"))
    engine.decide(state["request_id"], "u1001", approve=True)
    final = engine.execute(state["request_id"], simulate_failure="flaky")
    assert final["status"] == "COMPLETED"


def test_corrupt_tool_output_fails_safe(engine):
    engine.tools.failure_mode = "bad_data"                         # directory returns bad record
    state = engine.submit(load_request("allow/00_happy_path"))
    assert state["status"] == "FAILED_SAFE"
    assert "plan" not in state


# ---------------------------------------------------------------- 5. idempotency
def test_replay_does_not_create_duplicates(engine):
    raw = load_request("allow/00_happy_path")
    s1 = engine.submit(raw)
    engine.decide(s1["request_id"], "u1001", approve=True)
    engine.execute(s1["request_id"])

    s2 = engine.submit(raw)                       # same request again
    assert s2["request_id"] == s1["request_id"] and s2["replayed"]
    s3 = engine.execute(s1["request_id"])         # execute again
    assert s3["replayed"]
    assert len(engine.store.all()) == 1
    assert len(directory_grants(engine, "u1002")) == 1


def test_whitespace_variant_is_same_request(engine):
    raw = load_request("allow/00_happy_path")
    s1 = engine.submit(raw)
    s2 = engine.submit(raw | {"request_text": "  " + raw["request_text"].upper() + "  "})
    assert s1["request_id"] == s2["request_id"]


# ---------------------------------------------------------------- bonus: injection
class CompromisedModel:
    """Failure simulation: a compromised model that returns a malicious tool parameter.
    A real Gemini cannot be made to do this on demand, so this failure is simulated."""
    name = "compromised-model-simulation"

    def complete(self, system, user):
        return json.dumps({"intent": "grant_access", "system": "crm; DROP TABLE users",
                           "access_level": "write", "duration_days": 30,
                           "justification": "x", "gaps": [], "assumptions": []})


def test_llm_parameter_injection_fails_schema(engine_without_llm):
    eng = engine_without_llm
    eng.llm = CompromisedModel()
    state = eng.submit(load_request("allow/00_happy_path"))
    assert state["status"] == "FAILED_SAFE"            # schema rejected the value twice
    assert directory_grants(eng, "u1002") == []


def test_write_tool_rejects_admin_parameter(engine_without_llm):
    with pytest.raises(ToolSchemaError):
        engine_without_llm.call_tool("REQ-0000000000", "grant_access",
                         {"request_id": "REQ-0123456789", "user_id": "u1002", "system": "crm",
                          "access_level": "admin", "duration_days": 30}, "test")


def test_unknown_tool_is_refused(engine_without_llm):
    with pytest.raises(eng_mod.ToolNotAllowed):
        engine_without_llm.call_tool("REQ-0000000000", "delete_user", {"user_id": "u1002"}, "test")
