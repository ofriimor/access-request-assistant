"""
Tools = the functions the system is allowed to call on the "outside world".

Here the outside world is simulated by local files:
  - runtime/directory.json  -> the company Directory (users, roles, current access)
  - data/policies/*.md      -> the Knowledge Base (access policies)

Each tool is registered in TOOL_REGISTRY together with:
  - its input schema  (inputs are validated BEFORE the tool runs)
  - its output schema (outputs are validated AFTER the tool runs)
  - whether it changes state (side_effect=True -> needs approval)
"""
from __future__ import annotations

import json
import math
import re
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from pydantic import BaseModel

from . import schemas as sc


class ToolTransientError(Exception):
    """A temporary failure (timeout, 503). Worth retrying."""


class ToolPermanentError(Exception):
    """A failure that retrying will not fix (e.g. user not found)."""


# ---------------------------------------------------------------- policies
def parse_policy(path: Path) -> sc.Policy:
    header, _, body = path.read_text(encoding="utf-8").partition("\n---\n")
    meta = {}
    for line in header.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip()
    return sc.Policy(
        policy_id=meta["id"],
        title=meta["title"],
        system=meta["system"],
        allowed_departments=[d.strip() for d in meta["allowed_departments"].split(",")],
        max_access_level=meta["max_access_level"],
        default_duration_days=int(meta["default_duration_days"]),
        max_duration_days=int(meta["max_duration_days"]),
        text=body.strip(),
    )


_WORD = re.compile(r"[a-z_]+")
_STOP = {"the", "a", "an", "to", "and", "or", "of", "for", "i", "me", "my", "is", "in",
         "on", "be", "need", "please", "access", "because", "with", "this", "that", "are"}


def tokenize(text: str) -> list[str]:
    return [w for w in _WORD.findall(text.lower()) if w not in _STOP and len(w) > 1]


class PolicyIndex:
    """
    Knowledge Retrieval: a small TF-IDF keyword search over the policy files.
    For 5 documents this is enough, transparent and fully explainable.
    """

    def __init__(self, policy_dir: Path):
        self.policies = [parse_policy(p) for p in sorted(policy_dir.glob("*.md"))]
        self.docs = {p.policy_id: Counter(tokenize(f"{p.title} {p.system} {p.text}"))
                     for p in self.policies}
        n = len(self.docs)
        df = Counter(t for c in self.docs.values() for t in c)
        self.idf = {t: math.log((1 + n) / (1 + d)) + 1 for t, d in df.items()}

    def search(self, query: str, top_k: int) -> list[sc.PolicyHit]:
        q = tokenize(query)
        hits = []
        for p in self.policies:
            tf = self.docs[p.policy_id]
            score = sum((1 + math.log(tf[t])) * self.idf[t] for t in q if tf[t])
            snippet = p.text.splitlines()[0] if p.text else ""
            hits.append(sc.PolicyHit(policy_id=p.policy_id, title=p.title,
                                     score=round(score, 2), snippet=snippet))
        hits.sort(key=lambda h: h.score, reverse=True)
        return [h for h in hits[:top_k] if h.score > 0]

    def by_system(self, system: str) -> sc.Policy | None:
        return next((p for p in self.policies if p.system == system), None)

    def general(self) -> sc.Policy:
        return next(p for p in self.policies if p.system == "*")


# ---------------------------------------------------------------- toolbox
@dataclass
class ToolSpec:
    name: str
    input_model: type[BaseModel]
    side_effect: bool
    fn: Callable


class Toolbox:
    def __init__(self, directory_path: Path, policy_dir: Path):
        self.directory_path = directory_path
        self.index = PolicyIndex(policy_dir)
        self.failure_mode: str | None = None   # failure injection for demos/tests
        self._calls = Counter()

        # Allowlist: the ONLY tools that exist. Anything else is refused.
        self.registry: dict[str, ToolSpec] = {
            "lookup_user": ToolSpec("lookup_user", sc.LookupUserParams, False, self.lookup_user),
            "search_policies": ToolSpec("search_policies", sc.SearchPoliciesParams, False, self.search_policies),
            "read_policy": ToolSpec("read_policy", sc.ReadPolicyParams, False, self.read_policy),
            "grant_access": ToolSpec("grant_access", sc.GrantAccessParams, True, self.grant_access),
        }

    # -- directory file helpers
    def _load(self) -> dict:
        return json.loads(self.directory_path.read_text(encoding="utf-8"))

    def _save(self, data: dict) -> None:
        tmp = self.directory_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.directory_path)   # atomic replace: never a half-written file

    def _maybe_fail(self, tool: str) -> None:
        self._calls[tool] += 1
        if tool == "grant_access" and self.failure_mode == "timeout":
            raise ToolTransientError("Directory API timeout (simulated)")
        if tool == "grant_access" and self.failure_mode == "flaky" and self._calls[tool] == 1:
            raise ToolTransientError("Directory API 503 (simulated, first call only)")

    # -- READ tools
    def lookup_user(self, p: sc.LookupUserParams) -> sc.UserRecord:
        self._maybe_fail("lookup_user")
        user = next((u for u in self._load()["users"] if u["user_id"] == p.user_id), None)
        if user is None:
            raise ToolPermanentError(f"user {p.user_id} not found in directory")
        if self.failure_mode == "bad_data":
            user = {**user, "department": None}          # simulated corrupt response
        return sc.UserRecord.model_validate(user)        # output schema check

    def search_policies(self, p: sc.SearchPoliciesParams) -> list[sc.PolicyHit]:
        return self.index.search(p.query, p.top_k)

    def read_policy(self, p: sc.ReadPolicyParams) -> sc.Policy:
        pol = self.index.by_system(p.system)
        if pol is None:
            raise ToolPermanentError(f"no policy for system {p.system}")
        return pol

    # -- WRITE tool (changes state)
    def grant_access(self, p: sc.GrantAccessParams) -> sc.Grant:
        self._maybe_fail("grant_access")
        data = self._load()
        user = next((u for u in data["users"] if u["user_id"] == p.user_id), None)
        if user is None:
            raise ToolPermanentError(f"user {p.user_id} not found")
        # Idempotency at the tool level too: same request_id -> return existing grant.
        existing = next((g for g in user["access"] if g["request_id"] == p.request_id), None)
        if existing:
            return sc.Grant.model_validate(existing)
        now = datetime.now(timezone.utc)
        grant = {
            "grant_id": f"GRT-{uuid.uuid4().hex[:8]}",
            "system": p.system,
            "access_level": p.access_level,
            "request_id": p.request_id,
            "granted_at": now.isoformat(timespec="seconds"),
            "expires_at": (now + timedelta(days=p.duration_days)).isoformat(timespec="seconds"),
        }
        user["access"].append(grant)
        self._save(data)
        return sc.Grant.model_validate(grant)
