# Interview demo runbook (10–15 minutes)

## Before the interview (do not show keys)

1. Install: `python -m pip install -r requirements.txt`.
2. Set a **real** provider in the terminal (do not commit credentials):
   - PowerShell: `$env:GEMINI_API_KEY = Read-Host "Gemini API key"; $env:LLM_PROVIDER="gemini"; $env:LLM_MODEL="gemini-2.5-flash-lite"`
   - `gemini-2.5-flash-lite` is the model used in this project. API quotas/costs apply.
3. Check network and API access by running the interactive CLI once, or the full demo with `--require-real-llm`. The test suite uses an isolated FakeLLM and **does not** prove a real API call.
4. Run: `python demo.py --require-real-llm > evidence/live_demo_output.txt` (PowerShell redirection may use its own encoding). This resets local runtime. Confirm it prints `LLM provider: gemini`.
5. Preserve `runtime/audit.jsonl`, `runtime/test_results.xml`, and `reports/as_built.html` as the **actual** evidence from this run. Do not substitute old offline evidence. The generated report reflects the current run.

## Live demo, suggested timeline

| Time | Show | Explain |
| --- | --- | --- |
| 0:00–1:30 | `docs/ARCHITECTURE.md` and project tree | LLM extracts; deterministic Guard decides; manager approves; only then write |
| 1:30–4:30 | `python demo.py --pause --require-real-llm` scenario 1 | CRM request; retrieved policy IDs; plan hash; execute is blocked before approval; manager approves; read-back verifies |
| 4:30–6:00 | Scenario 2 and invalid input | `NEEDS_INFO` and Pydantic validation stop unsafe processing |
| 6:00–7:30 | Admin / injection cases | Untrusted prompt text is not authorization; policy and tool schemas constrain execution |
| 7:30–9:30 | Rejected manager and tool timeout | Reject prevents execution; bounded retry then `FAILED_SAFE`; recovery uses original approval |
| 9:30–11:00 | Replay | Repeated request and execution do not add duplicate grants |
| 11:00–13:00 | `runtime/audit.jsonl`, `runtime/test_results.xml`, `reports/as_built.html` | Trace events, automated tests and report are generated from the run |
| 13:00–15:00 | Q&A | Explain trade-offs and limitations |

**Alternative:** Run `python demo.py --require-real-llm` before the interview and present the saved evidence; then use `python -m app interactive` for a shorter live request. The interactive CLI simulates manager input, **not** authenticated SSO.

## Commands for targeted questions

```bash
python -m pytest -v
python -m app reset
python -m app submit data/requests/01_happy_path.json
# Copy the actual REQ-... ID from output; never use the placeholder below literally.
python -m app execute REQ-<actual-id>          # must be blocked
python -m app approve REQ-<actual-id> --by u1001
python -m app execute REQ-<actual-id>          # completes and verifies
python -m app audit REQ-<actual-id>
python -m app report
```

## Expected questions and defensible answers

- **Why isn't the LLM allowed to approve?** Its output is untrusted and schema-validated; the Guard makes deterministic policy decisions. Human approval is a separate gate.
- **Does `ALLOW` grant access?** No. It only permits creation of an approval-bound plan; execute requires a valid approval with the same plan hash.
- **How is replay handled?** Canonical request ID and idempotency key prevent duplicate writes for the same request/plan. This is a local demo, not a distributed exactly-once guarantee.
- **What if a write times out after the downstream system applied it?** Real integrations need a downstream idempotency key and reconciliation/read-back; the local mock does not prove distributed safety.
- **Is authentication real?** No. Requester/approver IDs are supplied in the CLI. Production requires SSO identity verification and authenticated approver actions.
- **Is access automatically revoked?** No. Expiry is recorded but a revocation worker is not implemented.
- **Why local policies and directory?** The task permits local simulated tools. Production would integrate with a real IdP/ITSM and versioned policies.
- **Did you use AI to write code?** Be transparent. The assignment explicitly allows AI-assisted development provided you understand the implementation.

## Submission checklist

- [ ] Source code and non-secret configuration included
- [ ] README and `docs/ARCHITECTURE.md` reviewed
- [ ] Real-provider demonstration run successfully completed, or limitation disclosed
- [ ] `python -m pytest -v` passed
- [ ] Real run's audit log, test XML and As-Built report retained
- [ ] 10–15-minute demo rehearsed
- [ ] Secrets, `.env`, and personal data excluded from submission
