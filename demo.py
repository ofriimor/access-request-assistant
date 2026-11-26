"""
The demo for the presentation, and the project's test harness in one command:

    python demo.py                    # every required scenario and check, then the As-Built report
    python demo.py --pause            # wait for Enter between parts (live demo)
    python demo.py --save-evidence    # also copy the report, audit and check results to evidence/

Every requirement of the assignment's sections 6 (demo scenarios) and 7 (tests) is an
explicit check, marked ✓ or ✗. The checks are written as JUnit XML (runtime/test_results.xml),
so the As-Built report lists them. Exit code 0 only if every check passed.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from xml.sax.saxutils import quoteattr

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")  # Windows console: box/check characters

from app.__main__ import show
from app.engine import (ApprovalError, ApprovalRequired, Engine, IntakeValidationError,
                        PROJECT_ROOT, ToolNotAllowed, ToolSchemaError, reset_runtime)
from app.report import generate

PAUSE = False
CHECKS: list[dict] = []          # every check of this run: name, requirement, ok, detail


def title(t: str) -> None:
    if PAUSE:
        input("\n[Enter] ")
    print("\n" + "═" * 72 + f"\n  {t}\n" + "═" * 72)


def step(t: str) -> None:
    print(f"\n▶ {t}")


def check(name: str, requirement: str, ok: bool, detail: str = "") -> bool:
    """Record one check and print it. name becomes the test name in the report."""
    CHECKS.append({"name": name, "requirement": requirement, "ok": bool(ok), "detail": detail})
    print(f"  {'✓' if ok else '✗'} CHECK  {name.replace('_', ' ')}" + (f"  ({detail})" if detail else ""))
    return bool(ok)


def load(name: str) -> dict:
    """A request from data/scenarios/, by scenario name."""
    from app.scenarios import request
    return request(name)


def grants_of(eng: Engine, user_id: str) -> list[dict]:
    users = json.loads(eng.tools.directory_path.read_text(encoding="utf-8"))["users"]
    return next(u for u in users if u["user_id"] == user_id)["access"]


def waiting(state: dict) -> bool:
    """False if the request did not reach the approval step (e.g. the LLM was unreachable)."""
    if state["status"] == "AWAITING_APPROVAL":
        return True
    if state.get("model_problem"):
        print("  ⚠ This part is skipped because the model is not available.")
    else:
        print(f"  (stopped at {state['status']} - this part is skipped)")
    return False


def write_junit(path: Path, seconds: float) -> None:
    """The checks as JUnit XML, the format the As-Built report reads."""
    failed = sum(not c["ok"] for c in CHECKS)
    rows = []
    for c in CHECKS:
        body = "" if c["ok"] else f'<failure message={quoteattr(c["detail"] or "check failed")}/>'
        rows.append(f'  <testcase classname={quoteattr("demo." + c["requirement"])} '
                    f'name={quoteattr("test_" + c["name"])} time="0">{body}</testcase>')
    path.write_text(f'<?xml version="1.0" encoding="utf-8"?>\n<testsuites><testsuite name="demo" '
                    f'tests="{len(CHECKS)}" failures="{failed}" errors="0" skipped="0" '
                    f'time="{seconds:.1f}">\n' + "\n".join(rows) + "\n</testsuite></testsuites>\n",
                    encoding="utf-8")


def main() -> int:
    global PAUSE
    from app.config import load_env
    load_env()   # GEMINI_API_KEY etc. from .env (never committed)
    ap = argparse.ArgumentParser()
    ap.add_argument("--pause", action="store_true")
    ap.add_argument("--save-evidence", action="store_true",
                    help="Copy the run's report, audit log and check results into evidence/")
    args = ap.parse_args()
    PAUSE = args.pause

    # Validate provider BEFORE reset so a missing API key cannot destroy evidence.
    from app.llm import get_llm
    try:
        get_llm()
    except ValueError as err:
        print(f"\n  ✗ LLM not configured: {err}\n    Put GEMINI_API_KEY in .env.\n")
        return 5
    reset_runtime()
    started = time.time()
    eng = Engine()
    print(f"\nLLM provider: {eng.llm.name} ({getattr(eng.llm, 'model', '')})")

    # ------------------------------------------------------------------ 1
    title("1. HAPPY PATH - Noa asks for CRM write access")
    step("submit  (Intake → Understand → Retrieve → Plan)")
    s = eng.submit(load("allow/00_happy_path")); show(s)
    if s.get("model_problem"):
        print("  ⚠ The demo stops here: the model (Gemini) is not available. Try again in a few minutes.")
        check("model_is_available", "setup", False, s.get("error", ""))
        return finish(eng, args, started)
    rid = s["request_id"]
    top = (s.get("knowledge") or [{}])[0].get("policy_id")
    check("knowledge_match_is_the_crm_policy", "knowledge_match", top == "POL-CRM-01", f"top source {top}")
    check("valid_request_waits_for_approval", "happy_path", s["status"] == "AWAITING_APPROVAL", s["status"])

    step("execute BEFORE approval → must be blocked")
    try:
        eng.execute(rid)
        check("execute_without_approval_is_blocked", "no_execute_without_approval", False, "it executed")
    except ApprovalRequired as e:
        check("execute_without_approval_is_blocked", "no_execute_without_approval", True, str(e))

    step("Omer (u1003, a colleague) tries to approve → must be refused")
    try:
        eng.decide(rid, "u1003", True)
        check("only_the_right_manager_can_approve", "no_execute_without_approval", False, "accepted")
    except ApprovalError as e:
        check("only_the_right_manager_can_approve", "no_execute_without_approval", True, str(e))

    step("approve by Dana (u1001, Noa's manager)")
    show(eng.decide(rid, "u1001", True, "covering northern region"))
    step("execute → verify")
    s = eng.execute(rid); show(s)
    check("approved_request_is_executed_and_verified", "happy_path",
          s["status"] == "COMPLETED" and s.get("verification", {}).get("ok"), s["status"])

    # ------------------------------------------------------------------ 2
    title("2. MISSING INFORMATION")
    s = eng.submit(load("needs_info/01_vague_request")); show(s)
    check("missing_information_stops_before_any_action", "missing_information",
          s["status"] == "NEEDS_INFO" and "plan" not in s, s["status"])

    title("2b. INVALID INPUT (schema)")
    try:
        eng.submit(load("invalid/03_missing_requester_and_short_text"))
        check("invalid_input_is_rejected", "input_validation", False, "accepted")
    except IntakeValidationError as e:
        print("  INPUT REJECTED:"); [print(f"    - {x}") for x in e.errors]
        check("invalid_input_is_rejected", "input_validation", True, f"{len(e.errors)} field errors")

    # ------------------------------------------------------------------ 3
    title("3a. BLOCKED BY POLICY - admin on production DB")
    s = eng.submit(load("blocked/01_admin_production_db")); show(s)
    check("admin_request_is_blocked", "rejected_or_blocked", s["status"] == "BLOCKED", s["status"])

    title("3b. BLOCKED - prompt injection in the request text")
    s = eng.submit(load("blocked/02_prompt_injection_english")); show(s)
    check("prompt_injection_is_blocked", "injection_bonus", s["status"] == "BLOCKED", s["status"])

    title("3c. REJECTED BY MANAGER")
    s = eng.submit(load("allow/10_support_crm_write_escalations")); show(s)
    if waiting(s):
        show(eng.decide(s["request_id"], "u1012", False, "read-only is enough for escalations"))
        step("try to execute anyway")
        try:
            eng.execute(s["request_id"])
            check("rejected_request_cannot_be_executed", "rejected_or_blocked", False, "it executed")
        except ApprovalRequired as e:
            check("rejected_request_cannot_be_executed", "rejected_or_blocked", True, str(e))
    else:
        check("rejected_request_cannot_be_executed", "rejected_or_blocked", False, f"stopped at {s['status']}")

    title("3d. BLOCKED - unauthorized tool calls (tool parameter injection)")
    step("grant_access with access_level=admin → schema must refuse it")
    try:
        eng.call_tool("REQ-0000000000", "grant_access",
                      {"request_id": "REQ-0123456789", "user_id": "u1002", "system": "crm",
                       "access_level": "admin", "duration_days": 30}, "demo")
        check("admin_parameter_is_refused_by_the_schema", "injection_bonus", False, "accepted")
    except ToolSchemaError as e:
        check("admin_parameter_is_refused_by_the_schema", "injection_bonus", True, str(e)[:70])
    step("a tool that is not on the allow-list → must be refused")
    try:
        eng.call_tool("REQ-0000000000", "delete_user", {"user_id": "u1002"}, "demo")
        check("unknown_tool_is_refused", "injection_bonus", False, "accepted")
    except ToolNotAllowed as e:
        check("unknown_tool_is_refused", "injection_bonus", True, str(e))

    # ------------------------------------------------------------------ 4
    title("4. TOOL FAILURE - directory times out")
    s = eng.submit(load("allow/11_manager_requests_for_employee")); rid2 = s["request_id"]
    if waiting(s):
        step(f"Dana submitted for Eden, so Dana's manager approves ({s['approver_required']})")
        eng.decide(rid2, s["approver_required"], True)
        step("execute with simulated timeout (3 attempts, backoff)")
        s = eng.execute(rid2, simulate_failure="timeout"); show(s)
        check("tool_timeout_stops_safely_without_a_grant", "tool_failure",
              s["status"] == "FAILED_SAFE" and not grants_of(eng, "u1009"), s["status"])
        step("directory is back → execute again (no new approval needed, idempotent)")
        s = eng.execute(rid2); show(s)
        check("after_the_failure_execute_again_completes", "tool_failure",
              s["status"] == "COMPLETED" and len(grants_of(eng, "u1009")) == 1, s["status"])
    else:
        check("tool_timeout_stops_safely_without_a_grant", "tool_failure", False, f"stopped at {s['status']}")

    title("4b. TOOL FAILURE - directory returns corrupt data")
    eng.tools.failure_mode = "bad_data"
    s = eng.submit(load("allow/02_marketing_crm_read_hebrew")); show(s)
    eng.tools.failure_mode = None
    check("corrupt_tool_output_stops_safely", "tool_failure",
          s["status"] == "FAILED_SAFE" and "plan" not in s, s["status"])

    # ------------------------------------------------------------------ 5
    title("5. REPLAY / IDEMPOTENCY - same request submitted and executed again")
    s = eng.submit(load("allow/00_happy_path")); show(s)
    check("same_request_returns_the_earlier_result", "idempotency", bool(s.get("replayed")), s["status"])
    show(eng.execute(rid))
    n = len(grants_of(eng, "u1002"))
    check("no_duplicate_grant", "idempotency", n == 1, f"{n} grant(s) for u1002")

    return finish(eng, args, started)


def finish(eng: Engine, args, started: float) -> int:
    title("CHECKS")
    for c in CHECKS:
        print(f"  {'✓' if c['ok'] else '✗'}  {c['requirement']:<30} {c['name'].replace('_', ' ')}")
    passed = sum(c["ok"] for c in CHECKS)
    print(f"\n  {passed}/{len(CHECKS)} checks passed")

    junit = PROJECT_ROOT / "runtime" / "test_results.xml"
    write_junit(junit, time.time() - started)
    report_path = generate(eng)
    print(f"\n  As-Built: {report_path}\n  Audit   : {eng.audit.path}")

    if args.save_evidence:
        import shutil
        ev = PROJECT_ROOT / "evidence"
        ev.mkdir(exist_ok=True)
        for src in (Path(report_path), eng.audit.path, junit):
            shutil.copy(src, ev / Path(src).name)
        print(f"  Evidence copied to: {ev}")
    return 0 if passed == len(CHECKS) else 1


if __name__ == "__main__":
    sys.exit(main())
