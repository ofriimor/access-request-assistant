"""
Recorded Gemini answers, used by LLM_PROVIDER=replay (see llm.ReplayLLM).

data/llm_recordings.json maps a request text to the JSON answer Gemini gave for it.
Every answer in the file came from the Gemini API; nothing is written by hand.

    python -m app record              # ask Gemini again for every scenario text (needs a key)
    python -m app record --from-audit evidence/scenarios_audit.jsonl
                                      # collect answers Gemini gave during earlier runs
"""
from __future__ import annotations

import json
from pathlib import Path

from .audit import now_iso

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RECORDINGS_PATH = PROJECT_ROOT / "data" / "llm_recordings.json"

# Request texts used by the tests that are not scenario files.
EXTRA_TEXTS = [
    "Please give me write access to the CRM for 60 days because I now handle the northern region deals",
]


def normalize(text: str) -> str:
    """Same rule as the request ID: whitespace and letter case do not matter."""
    return " ".join(text.split()).lower()


def load_recordings(path: Path | None = None) -> tuple[Path, dict]:
    path = path or RECORDINGS_PATH
    if not path.exists():
        raise ValueError(f"LLM_PROVIDER=replay needs {path} - create it with: python -m app record")
    data = json.loads(path.read_text(encoding="utf-8"))
    return path, {normalize(r["request_text"]): r for r in data["recordings"]}


def save_recordings(records: dict, path: Path | None = None) -> Path:
    path = path or RECORDINGS_PATH
    rows = sorted(records.values(), key=lambda r: r["request_text"])
    path.write_text(json.dumps({
        "about": "Answers the Gemini API gave for these request texts. Used only when "
                 "LLM_PROVIDER=replay (offline demo and tests). Regenerate: python -m app record",
        "recordings": rows,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def texts_to_record() -> list[str]:
    from . import scenarios
    out = []
    for p in scenarios.all_scenarios():
        if p.parent.name != "invalid":
            out.append(scenarios.request(p)["request_text"])
    return out + EXTRA_TEXTS


def from_audit(paths: list[Path]) -> dict:
    """Collect the answers Gemini gave in earlier runs, from audit logs."""
    found = {}
    for path in paths:
        texts = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            e = json.loads(line)
            if e["event"] in ("accepted", "retry_after_failed_safe"):
                texts[e["request_id"]] = e["data"]["input"]["request_text"]
            elif e["event"] == "llm_extracted" and e["data"].get("provider") == "gemini" \
                    and e["request_id"] in texts:
                t = texts[e["request_id"]]
                found[normalize(t)] = {"request_text": t, "answer": e["data"]["understanding"],
                                       "model": "gemini", "recorded_at": e["ts"],
                                       "source": f"audit log {path.name}"}
    return found


def record_live(llm, texts: list[str], log=print) -> dict:
    """Send every text to Gemini and keep the validated answers."""
    from .llm import SYSTEM_PROMPT, LLMOutputError, LLMServiceError, build_user_prompt, parse_understanding
    found = {}
    for t in texts:
        try:
            und = parse_understanding(llm.complete(SYSTEM_PROMPT, build_user_prompt(t)))
        except (LLMOutputError, LLMServiceError) as e:
            log(f"  ✗ {t[:60]}...  {e}")
            continue
        found[normalize(t)] = {"request_text": t, "answer": und.model_dump(),
                               "model": getattr(llm, "model", "gemini"), "recorded_at": now_iso(),
                               "source": "python -m app record"}
        log(f"  ✓ {t[:70]}")
    return found
