# Evidence

| File | What it shows | How it was produced |
| --- | --- | --- |
| `test_results.xml` | JUnit results of the 33 automated tests | `python -m pytest --junitxml=evidence/test_results.xml` |
| `pytest_output.txt` | Per-test PASS/FAIL list | `python -m pytest -v` |
| `as_built.html` | As-Built handover report of a full run | `python demo.py --require-real-llm --save-evidence` |
| `audit.jsonl` | Full audit trail of that run: steps, LLM extraction, tool calls, approvals, errors, final status | same run |

The automated tests use an isolated FakeLLM and do not call the Gemini API.
`as_built.html` and `audit.jsonl` come from a live run against Gemini (`gemini-3.5-flash-lite`).

To regenerate everything (requires `GEMINI_API_KEY` in `.env`):

```bash
python demo.py --require-real-llm --save-evidence
```
