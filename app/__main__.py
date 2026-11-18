"""
CLI:  python -m app <command>

  reset                                  fresh runtime (directory copy, empty state + audit)
  submit  <request.json>                 steps 1-4, stops for approval
  approve <REQ-id> --by <user_id>        step 5 (human approval)
  reject  <REQ-id> --by <user_id> --reason "..."
  execute <REQ-id> [--simulate-failure timeout|flaky]   steps 6-7
  status  <REQ-id>
  audit   [REQ-id]
  report                                 generate reports/as_built.html
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")  # Windows console: box/check characters

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
    if state.get("error"):
        print(f"  Error   : {state['error']}")
    print()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("reset")
    s = sub.add_parser("submit"); s.add_argument("file", type=Path)
    for name in ("approve", "reject"):
        p = sub.add_parser(name); p.add_argument("rid"); p.add_argument("--by", required=True)
        p.add_argument("--reason", default="")
    e = sub.add_parser("execute"); e.add_argument("rid")
    e.add_argument("--simulate-failure", choices=["timeout", "flaky"], default=None)
    st = sub.add_parser("status"); st.add_argument("rid")
    au = sub.add_parser("audit"); au.add_argument("rid", nargs="?")
    sub.add_parser("report")
    args = ap.parse_args(argv)

    if args.cmd == "reset":
        reset_runtime(); print("runtime reset."); return 0

    eng = Engine()
    try:
        if args.cmd == "submit":
            show(eng.submit(json.loads(args.file.read_text(encoding="utf-8"))))
        elif args.cmd in ("approve", "reject"):
            show(eng.decide(args.rid, args.by, args.cmd == "approve", args.reason))
        elif args.cmd == "execute":
            show(eng.execute(args.rid, args.simulate_failure))
        elif args.cmd == "status":
            show(eng.status(args.rid))
        elif args.cmd == "audit":
            for e_ in eng.audit.read(args.rid):
                print(json.dumps(e_, ensure_ascii=False))
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
