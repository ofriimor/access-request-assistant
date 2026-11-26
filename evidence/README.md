# Evidence

| File | What it shows | How it was produced |
| --- | --- | --- |
| `scenarios_as_built.html` | All 36 scenarios run live against Gemini, end to end, with the expected vs. the actual decision | `python -m app scenario --all --save-evidence` |
| `scenarios_audit.jsonl` | Audit trail of that run: steps, LLM extraction, tool calls, approvals, errors, final status | same run |
| `as_built.html` | As-Built report of the demo (the 5 required scenarios) | `python demo.py --save-evidence` |
| `audit.jsonl` | Audit trail of the demo run | same run |

To regenerate (requires `GEMINI_API_KEY` in `.env`):

```bash
python -m app reset
python demo.py --save-evidence
python -m app scenario --all --save-evidence
```
