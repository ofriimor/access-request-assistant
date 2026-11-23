# Evidence

| File | What it shows | How it was produced |
| --- | --- | --- |
| `test_results.xml` | JUnit results of the 72 automated tests (including the 34 scenarios in data/scenarios/), run against the real Gemini API | `python -m pytest --junitxml=evidence/test_results.xml` |
| `pytest_output.txt` | Per-test PASS/FAIL list | `python -m pytest -v` |
| `as_built.html` | As-Built handover report of a full run | `python demo.py --require-real-llm --save-evidence` |
| `audit.jsonl` | Full audit trail of that run: steps, LLM extraction, tool calls, approvals, errors, final status | same run |

All tests call the real Gemini API; only failures (tool timeouts, a compromised model, network outages) are simulated.
Generate the files with a valid `GEMINI_API_KEY` in `.env`.
`as_built.html` and `audit.jsonl` come from a live run against Gemini (`gemini-3.5-flash-lite`).

To regenerate everything (requires `GEMINI_API_KEY` in `.env`):

```bash
python demo.py --require-real-llm --save-evidence
```
