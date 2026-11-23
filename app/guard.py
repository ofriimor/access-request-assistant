"""
Guard = the deterministic "gatekeeper".

Principle: THE LLM SUGGESTS, THE CODE DECIDES.
The LLM only extracts what the user asked for. Whether it is allowed is decided
here, by plain Python rules that read the policy and the directory.
The LLM cannot talk its way past this file.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import schemas as sc

# Phrases that try to give orders to the system instead of describing a need.
INJECTION_PATTERNS = [
    r"ignore (all |any )?(previous|prior|above) (instructions|rules)",
    r"you are now",
    r"admin mode",
    r"approve (it )?automatically",
    r"auto[- ]?approve",
    r"system prompt",
    r"disregard (the )?(policy|rules)",
    r"bypass",
]


def detect_injection(text: str) -> list[str]:
    low = text.lower()
    return [p for p in INJECTION_PATTERNS if re.search(p, low)]


@dataclass
class Decision:
    verdict: str                      # ALLOW | NEEDS_INFO | BLOCK
    block_reasons: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    risk_level: str = "low"
    duration_days: int | None = None

    def to_dict(self) -> dict:
        return self.__dict__.copy()


def evaluate(
    und: sc.Understanding,
    requester: sc.UserRecord,
    target: sc.UserRecord,
    policy: sc.Policy | None,
    general: sc.Policy,
    injection_hits: list[str],
    current_request_id: str | None = None,
) -> Decision:
    """current_request_id: when the rules are checked again just before execution, a grant
    that this same request already wrote (e.g. before a crash) is not counted as a duplicate."""
    block: list[str] = []
    missing: list[str] = []
    assumptions: list[str] = list(und.assumptions)

    # --- safety first: injection is always blocked
    if injection_hits:
        block.append(f"{general.policy_id} §7: request text contains instructions to the system "
                     f"({len(injection_hits)} suspicious pattern(s)) - escalate to Security")

    # --- completeness (Missing Information)
    if und.intent != "grant_access":
        missing.append("intent: only 'grant access' requests are supported")
    if und.system is None:
        missing.append("system: which system is access needed for?")
    if und.access_level is None:
        missing.append("access_level: read or write?")
    if not und.justification:
        missing.append(f"justification: business reason is mandatory ({general.policy_id} §6)")
    missing += [f"llm-gap: {g}" for g in und.gaps if g]

    # --- identity rules
    if target.status != "active":
        block.append(f"{general.policy_id} §4: target user is {target.status}")
    if requester.user_id != target.user_id and requester.status != "active":
        block.append(f"{general.policy_id} §4: requester {requester.user_id} is {requester.status}")
    if requester.user_id != target.user_id and requester.user_id != target.manager_id:
        block.append(f"{general.policy_id} §2: requester {requester.user_id} is neither the "
                     f"employee nor their direct manager")
    if target.manager_id is None:
        block.append("no direct manager on record - nobody can approve")

    # --- policy rules (only if we know what is requested)
    duration = None
    if und.access_level == "admin":
        block.append(f"{general.policy_id} §1: admin access is never granted by the assistant")
    if und.system is not None:
        if policy is None:
            block.append(f"no policy found for system '{und.system}'")
        else:
            if "*" not in policy.allowed_departments and target.department not in policy.allowed_departments:
                block.append(f"{policy.policy_id}: department '{target.department}' is not allowed "
                             f"(allowed: {', '.join(policy.allowed_departments)})")
            if und.access_level and und.access_level != "admin" and \
                    sc.LEVEL_RANK[und.access_level] > sc.LEVEL_RANK[policy.max_access_level]:
                block.append(f"{policy.policy_id}: '{und.access_level}' exceeds the maximum "
                             f"'{policy.max_access_level}' for {policy.system}")
            # duration: default if missing, cap at the policy maximum
            duration = und.duration_days
            if duration is None:
                duration = policy.default_duration_days
                assumptions.append(f"no duration requested - using policy default {duration} days")
            elif duration > policy.max_duration_days:
                assumptions.append(f"requested {duration} days capped to policy max "
                                   f"{policy.max_duration_days}")
                duration = policy.max_duration_days
            # already has it? (no duplicate grants)
            if und.access_level:
                for g in target.access:
                    if g.request_id == current_request_id:
                        continue
                    if g.system == und.system and sc.LEVEL_RANK[g.access_level] >= sc.LEVEL_RANK.get(und.access_level, 9):
                        block.append(f"user already has {g.access_level} on {g.system} ({g.grant_id})")

    risk = "high" if (block or und.access_level == "admin") else (
        "medium" if und.access_level == "write" else "low")

    if block:
        verdict = "BLOCK"
    elif missing:
        verdict = "NEEDS_INFO"
    else:
        verdict = "ALLOW"
    return Decision(verdict, block, missing, assumptions, risk, duration)
