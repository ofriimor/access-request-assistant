# Evidence

| File | What it shows | How it was produced |
| --- | --- | --- |
| `test_results.xml` | JUnit results of the automated tests (including the 36 scenarios in data/scenarios/), run against the Gemini API | `python demo.py --tests --save-evidence` |
| `as_built.html` | As-Built handover report of a full run | `python demo.py --save-evidence` |
| `audit.jsonl` | Full audit trail of that run: steps, LLM extraction, tool calls, approvals, errors, final status | same run |
| `scenarios_as_built.html` | As-Built report of all 36 scenarios run live, end to end, with expected vs. actual decisions | `python -m app scenario --all --save-evidence` |
| `scenarios_audit.jsonl` | Audit trail of that scenario run | same run |

All tests call the Gemini API; only failures (tool timeouts, a compromised model, network outages) are simulated.
Generate the files with a valid `GEMINI_API_KEY` in `.env`.
`as_built.html` and `audit.jsonl` come from a live run against Gemini (`gemini-3.5-flash-lite`).

To regenerate everything (requires `GEMINI_API_KEY` in `.env`):

```bash
python demo.py --tests --save-evidence            # test results + demo report + audit
python -m app scenario --all --save-evidence      # all 36 scenarios: report + audit
```
