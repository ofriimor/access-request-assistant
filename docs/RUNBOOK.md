# Runbook — Access Request Assistant

## 1. Setup

1. Python 3.10+: `python -m pip install -r requirements.txt`
2. `copy .env.example .env` (Linux/macOS: `cp .env.example .env`) and set `GEMINI_API_KEY`.
   `.env` is git-ignored; never commit keys.
3. Sanity check: `python -m pytest -v`. With a key the tests call the Gemini API (uses quota);
   without one they replay recorded Gemini answers (`data/llm_recordings.json`). The pytest
   header line says which mode ran. Force a mode with `TEST_LLM=live` or `TEST_LLM=replay`.
4. Optional, before a live demo: `python -m app record` refreshes the recorded answers, so
   `python demo.py --replay` works even if the network or the Gemini API is down.

## 2. Run

| Goal | Command |
| --- | --- |
| Demo: the 5 required scenarios + As-Built report (~1 min) | `python demo.py` |
| Live demo with a pause between scenarios | `python demo.py --pause` |
| Same, offline (recorded Gemini answers) | `python demo.py --replay --pause` |
| Demo including the automated tests in the report | `python demo.py --tests` |
| Any CLI command offline | `python -m app --replay <command>` |
| Record / refresh Gemini answers for replay | `python -m app record` |
| Your own request, interactive manager decision | `python -m app interactive` |
| Reset local state (directory, SQLite, audit) | `python -m app reset` |

Outputs: `reports/as_built.html`, `runtime/audit.jsonl`, `runtime/test_results.xml`.
`demo.py` resets `runtime/` on every run; copy outputs to `evidence/` to keep them.

## 3. Step-by-step flow (CLI)

```bash
python -m app reset
python -m app submit allow/00_happy_path                # -> AWAITING_APPROVAL, prints REQ-<id>
python -m app execute REQ-<id>                           # blocked: no approval
python -m app approve REQ-<id> --by u1001                # direct manager approves
python -m app execute REQ-<id>                           # execute + read-back verify -> COMPLETED
python -m app execute REQ-<id>                           # replay -> no-op, no duplicate grant
python -m app audit   REQ-<id>
python -m app report
```

Reject: `python -m app reject REQ-<id> --by <manager_id> --reason "..."`
Simulate a tool failure: `python -m app execute REQ-<id> --simulate-failure timeout`

## 4. Scenarios (`data/scenarios/`)

All sample requests live in one folder, grouped by expected outcome:
`allow/` (12), `needs_info/` (7), `blocked/` (13), `invalid/` (4).
Each file holds a description, the request and the expected result.

| Goal | Command |
| --- | --- |
| List all scenarios | `python -m app scenario` |
| Run one live, end to end, and compare to the expected result | `python -m app scenario blocked/04_department_not_allowed` |
| ... with the manager rejecting | `python -m app scenario allow/10_support_crm_write_escalations --reject` |
| ... with a tool failure and recovery | `python -m app scenario allow/11_manager_requests_for_employee --simulate-failure timeout` |
| Run all, end to end, and build the As-Built report | `python -m app scenario --all` (add `--save-evidence` to copy it to `evidence/`) |
| Submit one into the step-by-step flow | `python -m app submit allow/00_happy_path` |
| Test all scenarios automatically | `python -m pytest tests/test_scenarios.py -v` |
| Test one scenario automatically | `python -m pytest tests/test_scenarios.py -v -k hebrew` |

A short name works when it is unique, e.g. `python -m app scenario 00_happy_path`.
To add a case, add a JSON file to the right folder; no code change is needed.

## 5. Request statuses

| Status | Meaning | Operator action |
| --- | --- | --- |
| `AWAITING_APPROVAL` | Plan built, waiting for the direct manager | `approve` / `reject` |
| `NEEDS_INFO` | Mandatory field missing | Resubmit with the missing details |
| `BLOCKED` | Policy / identity / injection rule, or a rule that failed when re-checked just before execute | No action via the assistant; escalate if needed |
| `REJECTED` | Manager rejected | None; execute is refused |
| `FAILED_SAFE` | LLM or tool failure; nothing unsafe was done | Before approval: submit the same request again (it re-runs). After approval: fix the cause, re-run `execute` (approval is kept) |
| `VERIFICATION_FAILED` | Read-back did not match the plan | Investigate directory state; see audit |
| `COMPLETED` | Granted and verified | None |

## 6. Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| Startup error about `GEMINI_API_KEY` | Key missing / `.env` not created | Create `.env` from `.env.example` |
| `LLM API HTTP 429` → FAILED_SAFE | Requests-per-minute quota still exceeded after 5 attempts (waits of 10–40 s) | Wait a minute, then resubmit; for `scenario --all` raise `--delay` (default 4 s) |
| `LLM API HTTP 5xx` → FAILED_SAFE | Provider outage (3 bounded retries done) | Wait, then resubmit |
| `LLM API connection failed` | Network / proxy / timeout (3 bounded attempts done) | Check connectivity; resubmit |
| `no recorded Gemini answer` | Replay mode, text was never recorded | Use a scenario, or run `python -m app record` with a key |
| `not the required approver` / `cannot approve it` | Wrong approver, or the approver submitted the request | Use the approver shown in `status` |
| `plan changed after approval` | Stored plan no longer matches the approved hash | Re-approve |
| Duplicate-looking request returns old record | Same content → same request ID (replay) | Expected behavior |

## 7. Known limitations

- Approver identity is supplied on the CLI and not authenticated (production: SSO + signed approval).
- If the direct manager submits a request, the manager's own manager must approve it.
- Directory and policies are local files simulating an IdP / ITSM; no file locking for concurrent writers.
- Expiry dates are recorded, but there is no automatic revocation job.
- Injection detection is a heuristic; safety relies on the deterministic Guard, closed enums and tool schemas.
