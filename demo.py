"""
One command that runs everything for the presentation:

    python demo.py            # run all scenarios, then build the As-Built report
    python demo.py --pause    # wait for Enter between scenarios (live demo)

Steps: reset -> run tests (saves evidence) -> 6 scenarios -> reports/as_built.html
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")  # Windows console: box/check characters

from app.__main__ import show
from app.engine import (ApprovalRequired, Engine, IntakeValidationError, PROJECT_ROOT,
                        reset_runtime)
from app.report import generate

REQ = PROJECT_ROOT / "data" / "requests"
PAUSE = False


def title(t: str) -> None:
    if PAUSE:
        input("\n[Enter] ")
    print("\n" + "═" * 72 + f"\n  {t}\n" + "═" * 72)


def step(t: str) -> None:
    print(f"\n▶ {t}")


def load(name: str) -> dict:
    return json.loads((REQ / name).read_text(encoding="utf-8"))


def main() -> None:
    global PAUSE
    ap = argparse.ArgumentParser()
    ap.add_argument("--pause", action="store_true")
    ap.add_argument("--skip-tests", action="store_true")
    PAUSE = ap.parse_args().pause or False

    reset_runtime()
    if not ap.parse_args().skip_tests:
        title("0. Automated tests")
        subprocess.run([sys.executable, "-m", "pytest", "-q",
                        f"--junitxml={PROJECT_ROOT / 'runtime' / 'test_results.xml'}"], cwd=PROJECT_ROOT)

    eng = Engine()
    print(f"\nLLM provider: {eng.llm.name}")

    # ------------------------------------------------------------------ 1
    title("1. HAPPY PATH - Noa asks for CRM write access")
    step("submit  (Intake → Understand → Retrieve → Plan)")
    s = eng.submit(load("01_happy_path.json")); show(s)
    rid = s["request_id"]
    step("execute BEFORE approval → must be blocked")
    try:
        eng.execute(rid)
    except ApprovalRequired as e:
        print(f"  ✗ BLOCKED: {e}")
    step("approve by Dana (u1001, Noa's manager)")
    show(eng.decide(rid, "u1001", True, "covering northern region"))
    step("execute → verify")
    show(eng.execute(rid))

    # ------------------------------------------------------------------ 2
    title("2. MISSING INFORMATION")
    show(eng.submit(load("02_missing_info.json")))

    title("2b. INVALID INPUT (schema)")
    try:
        eng.submit(load("07_invalid_input.json"))
    except IntakeValidationError as e:
        print("  ✗ INPUT REJECTED:"); [print(f"    - {x}") for x in e.errors]

    # ------------------------------------------------------------------ 3
    title("3a. BLOCKED BY POLICY - admin on production DB")
    show(eng.submit(load("03_blocked_admin.json")))

    title("3b. BLOCKED - prompt injection in the request text")
    show(eng.submit(load("04_prompt_injection.json")))

    title("3c. REJECTED BY MANAGER")
    s = eng.submit(load("05_rejected_by_manager.json")); show(s)
    show(eng.decide(s["request_id"], "u1012", False, "read-only is enough for escalations"))
    step("try to execute anyway")
    try:
        eng.execute(s["request_id"])
    except ApprovalRequired as e:
        print(f"  ✗ BLOCKED: {e}")

    # ------------------------------------------------------------------ 4
    title("4. TOOL FAILURE - directory times out")
    s = eng.submit(load("06_tool_failure.json")); rid2 = s["request_id"]
    eng.decide(rid2, "u1001", True)
    step("execute with simulated timeout (3 attempts, backoff)")
    show(eng.execute(rid2, simulate_failure="timeout"))
    step("directory is back → execute again (no new approval needed, idempotent)")
    show(eng.execute(rid2))

    # ------------------------------------------------------------------ 5
    title("5. REPLAY / IDEMPOTENCY - same request submitted and executed again")
    show(eng.submit(load("01_happy_path.json")))
    show(eng.execute(rid))
    grants = [g for u in json.loads(eng.tools.directory_path.read_text())["users"]
              if u["user_id"] == "u1002" for g in u["access"]]
    print(f"  Grants for u1002 in directory: {len(grants)}  (expected 1)")

    title("REPORT")
    print(f"  As-Built: {generate(eng)}")
    print(f"  Audit   : {eng.audit.path}")


if __name__ == "__main__":
    main()
