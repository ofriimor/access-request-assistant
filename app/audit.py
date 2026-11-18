"""
Audit Trail: an append-only log. One JSON line per event.
Nothing is ever edited or deleted here - it is the evidence of what happened.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class AuditLog:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def log(self, request_id: str | None, step: str, event: str, data: dict | None = None) -> None:
        entry = {
            "ts": now_iso(),
            "request_id": request_id,
            "step": step,
            "event": event,
            "data": data or {},
        }
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")

    def read(self, request_id: str | None = None) -> list[dict]:
        if not self.path.exists():
            return []
        out = []
        with self.path.open(encoding="utf-8") as f:
            for line in f:
                e = json.loads(line)
                if request_id is None or e["request_id"] == request_id:
                    out.append(e)
        return out
