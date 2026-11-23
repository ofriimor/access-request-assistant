# Runbook — Access Request Assistant

## 1. Setup

1. Python 3.10+: `python -m pip install -r requirements.txt`
2. `copy .env.example .env` (Linux/macOS: `cp .env.example .env`) and set `GEMINI_API_KEY`.
   `.env` is git-ignored; never commit keys.
3. Sanity check: `python -m pytest -q` (logic tests; no API key needed).
4. Live check against Gemini: `python -m pytest -m live -v` (needs the key; uses API quota).

## 2. Run

| Goal | Command |
| --- | --- |
| Full demo: tests, all scenarios, As-Built report | `python demo.py --require-real-llm` |
| Live demo with a pause between scenarios | `python demo.py --pause --require-real-llm` |
| Your own request, interactive manager decision | `python -m app interactive` |
| Reset local state (directory, SQLite, audit) | `python -m app reset` |

Outputs: `reports/as_built.html`, `runtime/audit.jsonl`, `runtime/test_results.xml`.
`demo.py` resets `runtime/` on every run; copy outputs to `evidence/` to keep them.

## 3. Step-by-step flow (CLI)

```bash
python -m app reset
python -m app submit data/requests/01_happy_path.json   # -> AWAITING_APPROVAL, prints REQ-<id>
python -m app execute REQ-<id>                           # blocked: no approval
python -m app approve REQ-<id> --by u1001                # direct manager approves
python -m app execute REQ-<id>                           # execute + read-back verify -> COMPLETED
python -m app execute REQ-<id>                           # replay -> no-op, no duplicate grant
python -m app audit   REQ-<id>
python -m app report
```

Reject: `python -m app reject REQ-<id> --by <manager_id> --reason "..."`
Simulate a tool failure: `python -m app execute REQ-<id> --simulate-failure timeout`

## 4. Demo scenarios (`data/requests/`)

| File | Scenario | Expected |
| --- | --- | --- |
| `01_happy_path.json` | CRM write for a Sales user | AWAITING_APPROVAL → APPROVED → COMPLETED |
| `02_missing_info.json` | Vague request | NEEDS_INFO, no plan |
| `03_blocked_admin.json` | Admin on prod_db | BLOCKED (POL-GEN-00 §1) |
| `04_prompt_injection.json` | Instructions to the system in the text | BLOCKED (§7) |
| `05_rejected_by_manager.json` | Manager rejects | REJECTED, execute refused |
| `06_tool_failure.json` | Directory timeout | FAILED_SAFE, re-run → COMPLETED |
| `07_invalid_input.json` | Missing `requester_id` | Input rejected, no record created |

## 5. Request statuses

| Status | Meaning | Operator action |
| --- | --- | --- |
| `AWAITING_APPROVAL` | Plan built, waiting for the direct manager | `approve` / `reject` |
| `NEEDS_INFO` | Mandatory field missing | Resubmit with the missing details |
| `BLOCKED` | Policy / identity / injection rule | No action via the assistant; escalate if needed |
| `REJECTED` | Manager rejected | None; execute is refused |
| `FAILED_SAFE` | LLM or tool failure; nothing unsafe was done | Fix cause, re-run `execute` (approval is kept) |
| `VERIFICATION_FAILED` | Read-back did not match the plan | Investigate directory state; see audit |
| `COMPLETED` | Granted and verified | None |

## 6. Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| Startup error about `GEMINI_API_KEY` | Key missing / `.env` not created | Create `.env` from `.env.example` |
| `LLM API HTTP 429/5xx` → FAILED_SAFE | Provider quota or outage (3 bounded retries done) | Wait, then resubmit |
| `LLM API connection failed` | Network / proxy | Check connectivity; resubmit |
| `not the required approver` | Approver is not the target's direct manager | Use the manager shown in `status` |
| `plan changed after approval` | Stored plan no longer matches the approved hash | Re-approve |
| Duplicate-looking request returns old record | Same content → same request ID (replay) | Expected behavior |

## 7. Known limitations

- Approver identity is supplied on the CLI and not authenticated (production: SSO + signed approval).
- Directory and policies are local files simulating an IdP / ITSM; no file locking for concurrent writers.
- Expiry dates are recorded, but there is no automatic revocation job.
- Injection detection is a heuristic; safety relies on the deterministic Guard, closed enums and tool schemas.
