"""
CLI:  python -m app <command>

  reset                                  fresh runtime (directory copy, empty state + audit)
  scenario                               list all scenarios (data/scenarios/)
  scenario <name>                        run one scenario live, end to end, and compare to the expected result
           [--reject] [--simulate-failure timeout] [--pause]
  scenario --all [--pause] [--save-evidence]   run every scenario end to end + As-Built report
  submit  <name | file.json>             steps 1-4, stops for approval
  approve <REQ-id> --by <user_id>        step 5 (human approval)
  reject  <REQ-id> --by <user_id> --reason "..."
  execute <REQ-id> [--simulate-failure timeout|flaky]   steps 6-7
  status  <REQ-id>
  audit   [REQ-id]
  report                                 generate reports/as_built.html
  interactive                            type your own request, then approve/reject it yourself
"""
from __future__ import annotations

import argparse
import json
import sys
import time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")  # Windows console: box/check characters

from . import scenarios
from .engine import (ApprovalError, ApprovalRequired, Engine, IntakeValidationError,
                     reset_runtime)


def show(state: dict) -> None:
    print(f"\n  Request : {state['request_id']}" + ("   (REPLAY - existing record)" if state.get("replayed") else ""))
    print(f"  Status  : {state['status']}")
    d = state.get("decision") or {}
    for r in d.get("block_reasons", []):
        print(f"  BLOCKED : {r}")
    for m in d.get("missing", []):
        print(f"  MISSING : {m}")
    for a in d.get("assumptions", []):
        print(f"  ASSUME  : {a}")
    if state.get("knowledge"):
        print("  Sources : " + ", ".join(f"{h['policy_id']} ({h['score']})" for h in state["knowledge"]))
    if state["status"] == "AWAITING_APPROVAL":
        p = state["plan"]
        print("\n  ┌─ APPROVAL NEEDED ─────────────────────────────────────────")
        print(f"  │ {p['summary']}")
        print(f"  │ tool={p['tool']}  risk={p['risk_level']}  policies={', '.join(p['policy_refs'])}")
        print(f"  │ plan_hash={state['plan_hash'][:16]}…  approver={state['approver_required']}")
        print("  └────────────────────────────────────────────────────────────")
    if state.get("approval"):
        a = state["approval"]
        print(f"  Approval: {'APPROVED' if a['approved'] else 'REJECTED'} by {a['approver_id']} {a['reason']}")
    if state.get("result"):
        r = state["result"]
        print(f"  Result  : {r['grant_id']} {r['system']}/{r['access_level']} expires {r['expires_at']}")
    if state.get("verification"):
        print(f"  Verify  : {'OK' if state['verification']['ok'] else 'FAILED'} {state['verification']['checks']}")
    if state.get("model_problem"):
        print("\n  ┌─ ⚠ MODEL PROBLEM ──────────────────────────────────────────")
        print(f"  │ {state['model_problem']}")
        print(f"  │ Details: {state.get('error')}")
        print("  └────────────────────────────────────────────────────────────")
    elif state.get("error"):
        print(f"  Error   : {state['error']}")
    print()


def list_scenarios() -> None:
    labels = {"allow": "ALLOW      -> AWAITING_APPROVAL", "needs_info": "NEEDS_INFO -> NEEDS_INFO",
              "blocked": "BLOCKED    -> BLOCKED", "invalid": "INVALID    -> rejected at intake"}
    for group in scenarios.GROUPS:
        print(f"\n  {labels[group]}")
        for path in scenarios.all_scenarios():
            if path.parent.name == group:
                print(f"    {scenarios.name_of(path):<48} {scenarios.load(path)['description']}")
    print("\n  Run one:  python -m app scenario <name>      e.g.  python -m app scenario allow/00_happy_path")
    print("  Run all:  python -m app scenario --all       (end to end, then the As-Built report)\n")


def _step(text: str, pause: bool) -> None:
    if pause:
        input(f"\n  [Enter] {text} ")
    else:
        print(f"\n  ▶ {text}")


def run_scenario(eng: Engine, ref: str, *, reject: bool = False, failure: str | None = None,
                 pause: bool = False, quiet: bool = False) -> dict:
    """Run one scenario live from start to finish.

    1-4  the request goes through Intake, Gemini, Retrieve and the Guard
    then, if the decision is ALLOW:
         execute before approval (must be blocked) -> manager approves (or rejects)
         -> execute (optionally with a simulated tool failure, then a re-run) -> verify
    Returns the expected decision, the decision reached and the final status."""
    out = print if not quiet else (lambda *a, **k: None)
    show_ = show if not quiet else (lambda s: None)
    path = scenarios.resolve(ref)
    case = scenarios.load(path)
    expected = case["expect"].get("status", "?")
    out(f"\n  Scenario: {scenarios.name_of(path)}")
    out(f"  About   : {case['description']}")
    out(f"  Text    : {case['request'].get('request_text', '')}")

    try:
        state = eng.submit(case["request"])
    except IntakeValidationError as err:
        out("\n  ✗ INPUT REJECTED (nothing was created, the model was not called):")
        for x in err.errors:
            out(f"    - {x}")
        decision = final = "INPUT_REJECTED"
    else:
        show_(state)
        und = state.get("understanding") or {}
        if und:
            out(f"  Gemini understood: system={und.get('system')} level={und.get('access_level')} "
                f"duration={und.get('duration_days')} intent={und.get('intent')}")
        decision = final = state["status"]

        if state["status"] == "AWAITING_APPROVAL":
            rid, approver = state["request_id"], state["approver_required"]
            _step("execute BEFORE approval (must be blocked)", pause)
            try:
                eng.execute(rid)
                out("  ✗ executed without approval - this must never happen")
            except ApprovalRequired as err:
                out(f"  ✓ BLOCKED: {err}")
            _step(f"manager {approver} {'rejects' if reject else 'approves'}", pause)
            state = eng.decide(rid, approver, not reject, "scenario run")
            show_(state)
            if reject:
                _step("execute after the rejection (must be blocked)", pause)
                try:
                    eng.execute(rid)
                except ApprovalRequired as err:
                    out(f"  ✓ BLOCKED: {err}")
            else:
                if failure:
                    _step(f"execute with a simulated tool failure ({failure})", pause)
                    show_(eng.execute(rid, simulate_failure=failure))
                    _step("the tool is back: execute again (same approval, no duplicate)", pause)
                else:
                    _step("execute and verify", pause)
                state = eng.execute(rid)
                show_(state)
            final = eng.status(rid)["status"]

    ok = decision == expected
    model_down = decision == "FAILED_SAFE" and bool(eng.status(state["request_id"]).get("model_problem")) \
        if decision not in ("INPUT_REJECTED",) else False
    out("  ─────────────────────────────────────────────────────────────")
    if model_down:
        decision = final = "MODEL_UNAVAILABLE"
        out("  System decision : not checked - the model (Gemini) is not available, see above")
    else:
        out(f"  System decision : {decision:<18} expected {expected:<18} {'✓' if ok else '✗ DIFFERENT'}")
    out(f"  Final status    : {final}" + ("   (after the manager's decision)" if final != decision else ""))
    out("")
    return {"name": scenarios.name_of(path), "expected": expected, "decision": decision,
            "final": final, "ok": ok}


def run_all_scenarios(eng: Engine, pause: bool = False, save_evidence: bool = False,
                      delay: float = 4.0) -> int:
    """Every scenario, live and end to end, then one As-Built report for the whole run.
    The directory is restored before each scenario, so they do not affect each other."""
    import shutil
    from .engine import PROJECT_ROOT
    from .report import generate
    original = PROJECT_ROOT / "data" / "directory.json"
    paths = scenarios.all_scenarios()
    results = []
    for n, path in enumerate(paths, 1):
        if pause:
            input(f"\n[Enter] scenario {n}/{len(paths)}: {scenarios.name_of(path)} ")
        else:
            print(f"\n{'═' * 72}\n  {n}/{len(paths)}  {scenarios.name_of(path)}\n{'═' * 72}")
        shutil.copy(original, eng.tools.directory_path)
        results.append(run_scenario(eng, path))
        if delay and not pause and n < len(paths) and results[-1]["decision"] != "INPUT_REJECTED":
            time.sleep(delay)          # stay under the API's requests-per-minute quota

    print(f"\n{'═' * 72}\n  SUMMARY\n{'═' * 72}")
    for r in results:
        mark = "⚠" if r["decision"] == "MODEL_UNAVAILABLE" else ("✓" if r["ok"] else "✗")
        print(f"  {mark}  {r['name']:<48} decision {r['decision']:<18} final {r['final']}")
    passed = sum(r["ok"] for r in results)
    down = sum(r["decision"] == "MODEL_UNAVAILABLE" for r in results)
    print(f"\n  {passed}/{len(results)} scenarios reached the expected decision")
    if down:
        print(f"  ⚠ {down} scenario(s) could not be checked: the model (Gemini) was not available. "
              "Run them again later.")
    report = generate(eng)
    print(f"  As-Built: {report}\n  Audit   : {eng.audit.path}")
    if save_evidence:
        ev = PROJECT_ROOT / "evidence"
        ev.mkdir(exist_ok=True)
        shutil.copy(report, ev / "scenarios_as_built.html")
        shutil.copy(eng.audit.path, ev / "scenarios_audit.jsonl")
        print(f"  Evidence: {ev / 'scenarios_as_built.html'}, {ev / 'scenarios_audit.jsonl'}")
    print()
    return 0 if passed == len(results) else 1


def interactive(eng: Engine) -> None:
    """You type a new request, then you play the manager. Nothing is scripted."""
    users = json.loads(eng.tools.directory_path.read_text(encoding="utf-8"))["users"]
    print("\n  Employees in the directory:")
    for u in users:
        print(f"    {u['user_id']}  {u['name']:<16} {u['department']:<12} manager={u['manager_id']}  {u['status']}")
    print()
    target = input("  Employee who needs access (e.g. u1002): ").strip()
    requester = input(f"  Who is submitting? [Enter = {target}]: ").strip() or target
    text = input("  Request text (free text, English):\n  > ").strip()

    state = eng.submit({"requester_id": requester, "target_user_id": target, "request_text": text})
    show(state)
    if state["status"] != "AWAITING_APPROVAL":
        print("  The system stopped here - nothing was sent for approval, nothing was executed.\n")
        return

    rid = state["request_id"]
    print(f"  ⏸  Waiting for approval by the direct manager ({state['approver_required']}).")
    approver = input("  Approve as which user id? ").strip()
    answer = input("  Approve? (y/n): ").strip().lower()
    reason = input("  Reason (optional): ").strip()
    try:
        state = eng.decide(rid, approver, answer == "y", reason)
    except ApprovalError as err:
        print(f"\n  ✗ APPROVAL REFUSED: {err}\n"); return
    show(state)
    if state["status"] == "APPROVED":
        show(eng.execute(rid))


def main(argv=None) -> int:
    from .config import load_env
    load_env()   # GEMINI_API_KEY etc. from .env (never committed)
    ap = argparse.ArgumentParser(prog="python -m app")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("reset")
    s = sub.add_parser("submit"); s.add_argument("file", help="scenario name or path to a JSON file")
    sc = sub.add_parser("scenario", help="list, run one, or run all scenarios end to end")
    sc.add_argument("name", nargs="?")
    sc.add_argument("--all", action="store_true", help="run every scenario and build the As-Built report")
    sc.add_argument("--reject", action="store_true", help="the manager rejects instead of approving")
    sc.add_argument("--simulate-failure", choices=["timeout", "flaky"], default=None)
    sc.add_argument("--pause", action="store_true", help="wait for Enter between steps")
    sc.add_argument("--save-evidence", action="store_true")
    sc.add_argument("--delay", type=float, default=4.0,
                    help="seconds between scenarios in --all, to respect the API rate limit (default 4)")
    for name in ("approve", "reject"):
        p = sub.add_parser(name); p.add_argument("rid"); p.add_argument("--by", required=True)
        p.add_argument("--reason", default="")
    e = sub.add_parser("execute"); e.add_argument("rid")
    e.add_argument("--simulate-failure", choices=["timeout", "flaky"], default=None)
    st = sub.add_parser("status"); st.add_argument("rid")
    au = sub.add_parser("audit"); au.add_argument("rid", nargs="?")
    sub.add_parser("report")
    sub.add_parser("interactive")
    args = ap.parse_args(argv)

    if args.cmd == "reset":
        reset_runtime(); print("runtime reset."); return 0
    if args.cmd == "scenario" and args.all:
        reset_runtime()
    elif args.cmd == "scenario" and not args.name:
        list_scenarios(); return 0
    if args.cmd == "submit" or (args.cmd == "scenario" and not args.all):
        try:
            scenarios.resolve(args.file if args.cmd == "submit" else args.name)
        except FileNotFoundError as err:
            print(f"\n  ✗ {err}\n"); return 6

    try:
        eng = Engine()
    except ValueError as err:          # e.g. GEMINI_API_KEY not set
        print(f"\n  ✗ LLM not configured: {err}\n"
              "    Copy .env.example to .env and put your key in it.\n")
        return 5
    try:
        if args.cmd == "submit":
            show(eng.submit(scenarios.request(args.file)))
        elif args.cmd == "scenario" and args.all:
            return run_all_scenarios(eng, args.pause, args.save_evidence, args.delay)
        elif args.cmd == "scenario":
            result = run_scenario(eng, args.name, reject=args.reject,
                                  failure=args.simulate_failure, pause=args.pause)
            return 0 if result["ok"] else 1
        elif args.cmd in ("approve", "reject"):
            show(eng.decide(args.rid, args.by, args.cmd == "approve", args.reason))
        elif args.cmd == "execute":
            show(eng.execute(args.rid, args.simulate_failure))
        elif args.cmd == "status":
            show(eng.status(args.rid))
        elif args.cmd == "audit":
            for e_ in eng.audit.read(args.rid):
                print(json.dumps(e_, ensure_ascii=False))
        elif args.cmd == "interactive":
            interactive(eng)
        elif args.cmd == "report":
            from .report import generate
            print(f"written: {generate(eng)}")
    except IntakeValidationError as err:
        print("\n  ✗ INPUT REJECTED (nothing was created):")
        for x in err.errors:
            print(f"    - {x}")
        return 2
    except (ApprovalRequired, ApprovalError) as err:
        print(f"\n  ✗ BLOCKED: {err}\n"); return 3
    except KeyError as err:
        print(f"\n  ✗ {err}\n"); return 4
    except FileNotFoundError as err:
        print(f"\n  ✗ {err}\n"); return 6
    return 0


if __name__ == "__main__":
    sys.exit(main())
