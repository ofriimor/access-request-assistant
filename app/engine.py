"""
Engine = the orchestrator. Implements the given functional design:

  submit():   1 Intake -> 2 Understand -> 3 Retrieve -> 4 Plan   (then STOPS)
  approve():  5 Approve   (a human decision, recorded against the exact plan)
  execute():  6 Execute -> 7 Verify    (only if approved, only once)

Every step: updates the state (state.py) and writes to the audit log (audit.py).
"""
from __future__ import annotations

import hashlib
import json
import shutil
import time
from pathlib import Path

from pydantic import ValidationError

from . import guard
from . import schemas as sc
from .audit import AuditLog, now_iso
from .llm import SYSTEM_PROMPT, LLMOutputError, LLMServiceError, build_user_prompt, get_llm, parse_understanding
from .state import StateStore, Status, transition
from .tools import ToolPermanentError, ToolTransientError, Toolbox

PROJECT_ROOT = Path(__file__).resolve().parent.parent

MODEL_PROBLEM = ("The language model (Gemini) is not available right now. The request was not "
                 "processed and nothing was executed. Please try again in a few minutes.")


# ------------------------------------------------------------- errors
class IntakeValidationError(Exception):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("Invalid request: " + "; ".join(errors))


class ApprovalRequired(Exception):
    """Raised when someone tries to execute without a valid approval."""


class ApprovalError(Exception):
    pass


class ToolNotAllowed(Exception):
    pass


class ToolSchemaError(Exception):
    pass


class ToolFailed(Exception):
    pass


class UnknownUser(Exception):
    """A user id in the request does not exist in the directory - a business stop, not an outage."""


# ------------------------------------------------------------- helpers
def canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def sha(obj) -> str:
    return hashlib.sha256(canonical(obj).encode()).hexdigest()


def make_request_id(req: sc.AccessRequestInput) -> str:
    # Same content -> same ID. This is what makes a re-submitted request a "replay".
    body = req.model_dump()
    body["request_text"] = " ".join(body["request_text"].split()).lower()
    return "REQ-" + sha(body)[:10]


def pydantic_errors(e: ValidationError) -> list[str]:
    return [f"{'.'.join(str(x) for x in err['loc']) or 'body'}: {err['msg']}" for err in e.errors()]


class Engine:
    MAX_RETRIES = 3

    def __init__(self, runtime_dir: Path | None = None, data_dir: Path | None = None,
                 llm=None, sleep=time.sleep):
        self.data_dir = data_dir or PROJECT_ROOT / "data"
        self.runtime_dir = runtime_dir or PROJECT_ROOT / "runtime"
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        directory = self.runtime_dir / "directory.json"
        if not directory.exists():
            shutil.copy(self.data_dir / "directory.json", directory)
        self.store = StateStore(self.runtime_dir / "state.db")
        self.audit = AuditLog(self.runtime_dir / "audit.jsonl")
        self.tools = Toolbox(directory, self.data_dir / "policies")
        self.llm = llm or get_llm()
        self.sleep = sleep

    # ================================================================ tools
    def call_tool(self, rid: str, name: str, raw_params: dict, step: str):
        """The ONLY way the engine touches a tool: allowlist -> schema -> retry -> audit."""
        spec = self.tools.registry.get(name)
        if spec is None:
            self.audit.log(rid, step, "tool_blocked", {"tool": name, "reason": "not in allowlist"})
            raise ToolNotAllowed(f"tool '{name}' is not allowed")
        try:
            params = spec.input_model.model_validate(raw_params)
        except ValidationError as e:
            errs = pydantic_errors(e)
            self.audit.log(rid, step, "tool_schema_rejected", {"tool": name, "params": raw_params, "errors": errs})
            raise ToolSchemaError(f"{name}: invalid parameters: {errs}") from e

        for attempt in range(1, self.MAX_RETRIES + 1):
            try:
                result = spec.fn(params)
                out = [r.model_dump() for r in result] if isinstance(result, list) else result.model_dump()
                self.audit.log(rid, step, "tool_call", {"tool": name, "params": params.model_dump(),
                                                        "attempt": attempt, "ok": True,
                                                        "result": _short(out)})
                return result
            except ToolTransientError as e:
                self.audit.log(rid, step, "tool_error", {"tool": name, "attempt": attempt,
                                                         "error": str(e), "retryable": True})
                if attempt < self.MAX_RETRIES:
                    self.sleep(0.2 * 2 ** (attempt - 1))       # exponential backoff
            except (ToolPermanentError, ValidationError) as e:
                kind = "invalid tool output" if isinstance(e, ValidationError) else "permanent error"
                self.audit.log(rid, step, "tool_error", {"tool": name, "attempt": attempt,
                                                         "error": f"{kind}: {str(e)[:300]}",
                                                         "retryable": False})
                raise ToolFailed(f"{name}: {kind}: {str(e)[:200]}") from e
        raise ToolFailed(f"{name}: failed after {self.MAX_RETRIES} attempts")

    # ================================================================ 1-4 submit
    def submit(self, raw: dict) -> dict:
        # ---- 1. Intake
        try:
            req = sc.AccessRequestInput.model_validate(raw)
        except ValidationError as e:
            errs = pydantic_errors(e)
            self.audit.log(None, "intake", "validation_failed", {"errors": errs, "input": raw})
            raise IntakeValidationError(errs) from e

        rid = make_request_id(req)
        existing = self.store.get(rid)
        # A request that stopped on an outage BEFORE any approval (Gemini down, directory
        # timeout) is run again; nothing was approved or executed, so this is safe.
        retry = existing is not None and existing["status"] == Status.FAILED_SAFE.value \
            and not existing.get("approval")
        if existing and not retry:
            self.audit.log(rid, "intake", "replay_detected",
                           {"status": existing["status"], "action": "returned existing record, nothing re-run"})
            existing["replayed"] = True
            return existing

        state = {"request_id": rid, "status": Status.RECEIVED.value, "input": req.model_dump(),
                 "created_at": now_iso(), "llm_provider": self.llm.name,
                 "history": [{"ts": now_iso(), "status": "RECEIVED", "note": "intake ok"}]}
        if retry:
            state["created_at"] = existing["created_at"]
            state["history"] = existing["history"] + [
                {"ts": now_iso(), "status": "RECEIVED", "note": "re-submitted after a safe stop"}]
            self.store.save(state)
            self.audit.log(rid, "intake", "retry_after_failed_safe",
                           {"previous_error": existing.get("error"), "input": req.model_dump()})
        else:
            self.store.create(state)
            self.audit.log(rid, "intake", "accepted", {"input": req.model_dump()})

        try:
            self._understand(state)
            self._retrieve(state)
            self._plan(state)
        except UnknownUser as e:
            state["decision"] = {"verdict": "BLOCK", "block_reasons": [str(e)], "missing": [],
                                 "assumptions": [], "risk_level": "high", "duration_days": None}
            transition(state, Status.BLOCKED, str(e))
            self.audit.log(rid, "retrieve", "unknown_user", {"reason": str(e)})
        except (LLMOutputError, LLMServiceError) as e:
            # The model is not available or answered nonsense: tell the user plainly.
            state["error"] = str(e)
            state["model_problem"] = MODEL_PROBLEM
            transition(state, Status.FAILED_SAFE, f"model problem, stopped before any action: {e}")
            self.audit.log(rid, "understand", "model_unavailable", {"error": str(e)})
        except (ToolFailed, ToolSchemaError, ToolNotAllowed) as e:
            state["error"] = str(e)
            transition(state, Status.FAILED_SAFE, f"stopped before any action: {e}")
            self.audit.log(rid, "submit", "failed_safe", {"error": str(e)})
        self.store.save(state)
        return state

    # ---- 2. Understand (LLM)
    def _understand(self, state: dict) -> None:
        rid, text = state["request_id"], state["input"]["request_text"]
        user_prompt = build_user_prompt(text)
        last_err = None
        for attempt in (1, 2):
            prompt = user_prompt if attempt == 1 else (
                user_prompt + f"\n\nYour previous answer was invalid: {last_err}. Return valid JSON only.")
            raw = self.llm.complete(SYSTEM_PROMPT, prompt)
            try:
                und = parse_understanding(raw)
                break
            except LLMOutputError as e:
                last_err = str(e)[:300]
                self.audit.log(rid, "understand", "llm_output_invalid", {"attempt": attempt, "error": last_err,
                                                                         "raw": raw[:500]})
        else:
            raise LLMOutputError(f"LLM output invalid twice: {last_err}")

        state["understanding"] = und.model_dump()
        state["injection_hits"] = guard.detect_injection(text)
        transition(state, Status.UNDERSTOOD)
        self.audit.log(rid, "understand", "llm_extracted", {"provider": self.llm.name,
                                                            "understanding": und.model_dump(),
                                                            "injection_hits": state["injection_hits"]})
        self.store.save(state)

    # ---- 3. Retrieve (Knowledge Base + Directory)
    def _retrieve(self, state: dict) -> None:
        rid, inp, und = state["request_id"], state["input"], state["understanding"]
        query = " ".join(filter(None, [und["system"], und["access_level"], inp["request_text"]]))
        hits = self.call_tool(rid, "search_policies", {"query": query, "top_k": 3}, "retrieve")
        state["knowledge"] = [h.model_dump() for h in hits]

        target = self._lookup_known_user(rid, inp["target_user_id"], "target user")
        requester = target if inp["requester_id"] == inp["target_user_id"] else \
            self._lookup_known_user(rid, inp["requester_id"], "requester")
        state["target_user"] = target.model_dump()
        state["requester"] = requester.model_dump()
        state["policy"] = None
        if und["system"]:
            state["policy"] = self.call_tool(rid, "read_policy", {"system": und["system"]},
                                             "retrieve").model_dump(exclude={"text"})
        # The policies the rules check: the general policy always, plus the system's policy.
        # (Not the same as the search results above, which only show the closest sources.)
        state["policies_applied"] = [self.tools.index.general().policy_id] + \
            ([state["policy"]["policy_id"]] if state["policy"] else [])
        transition(state, Status.RETRIEVED)
        self.store.save(state)

    def _lookup_known_user(self, rid: str, user_id: str, role: str) -> sc.UserRecord:
        try:
            return self.call_tool(rid, "lookup_user", {"user_id": user_id}, "retrieve")
        except ToolFailed as e:
            if isinstance(e.__cause__, ToolPermanentError):
                raise UnknownUser(f"{role} {user_id} does not exist in the directory") from e
            raise

    # ---- 4. Plan (guard decides, then a plan is built)
    def _plan(self, state: dict) -> None:
        rid = state["request_id"]
        und = sc.Understanding.model_validate(state["understanding"])
        policy = self.tools.index.by_system(und.system) if und.system else None
        decision = guard.evaluate(und, sc.UserRecord.model_validate(state["requester"]),
                                  sc.UserRecord.model_validate(state["target_user"]),
                                  policy, self.tools.index.general(), state["injection_hits"])
        state["decision"] = decision.to_dict()
        self.audit.log(rid, "plan", "guard_decision", decision.to_dict())

        if decision.verdict == "BLOCK":
            transition(state, Status.BLOCKED, "; ".join(decision.block_reasons))
            return
        if decision.verdict == "NEEDS_INFO":
            transition(state, Status.NEEDS_INFO, "; ".join(decision.missing))
            return

        approver_id, problem = self._required_approver(rid, state)
        if problem:
            decision.verdict, decision.risk_level = "BLOCK", "high"
            decision.block_reasons.append(problem)
            state["decision"] = decision.to_dict()
            transition(state, Status.BLOCKED, problem)
            self.audit.log(rid, "plan", "approver_blocked", {"reason": problem})
            return

        target = state["target_user"]
        plan = sc.ActionPlan(
            tool="grant_access",
            params=sc.GrantAccessParams(request_id=rid, user_id=target["user_id"], system=und.system,
                                        access_level=und.access_level, duration_days=decision.duration_days),
            policy_refs=[self.tools.index.general().policy_id, policy.policy_id],
            risk_level=decision.risk_level,
            summary=(f"Grant {und.access_level.upper()} access on {und.system} to {target['name']} "
                     f"({target['user_id']}, {target['department']}) for {decision.duration_days} days. "
                     f"Reason: {und.justification}"),
        )
        state["plan"] = plan.model_dump()
        state["plan_hash"] = sha(state["plan"])
        state["approver_required"] = approver_id
        transition(state, Status.AWAITING_APPROVAL, f"waiting for approver {approver_id}")
        self.audit.log(rid, "plan", "plan_created", {"plan": state["plan"], "plan_hash": state["plan_hash"],
                                                     "approver_required": approver_id})

    def _required_approver(self, rid: str, state: dict) -> tuple[str | None, str | None]:
        """Who must approve (POL-GEN-00 §3, §4). Returns (approver_id, block_reason).
        Normally the target's direct manager. When that manager submitted the request,
        their own manager approves, so nobody approves their own request."""
        requester, target = state["requester"], state["target_user"]
        approver_id = target["manager_id"]
        if requester["user_id"] == approver_id:
            approver_id = requester["manager_id"]
            if approver_id is None:
                return None, (f"POL-GEN-00 §3: the request was submitted by the direct manager "
                              f"{requester['user_id']}, who has no manager to approve it independently")
        approver = self._lookup_known_user(rid, approver_id, "approver")
        if approver.status != "active":
            return None, f"POL-GEN-00 §4: approver {approver_id} is {approver.status}"
        return approver_id, None

    # ================================================================ 5 approve
    def decide(self, rid: str, approver_id: str, approve: bool, reason: str = "") -> dict:
        state = self._load(rid)
        if state["status"] != Status.AWAITING_APPROVAL.value:
            self.audit.log(rid, "approve", "approval_refused", {"reason": f"status is {state['status']}"})
            raise ApprovalError(f"request is {state['status']}, not AWAITING_APPROVAL")
        if approver_id != state["approver_required"]:
            self.audit.log(rid, "approve", "approval_refused",
                           {"approver": approver_id, "reason": "not the required approver"})
            raise ApprovalError(f"{approver_id} is not authorized; required approver is {state['approver_required']}")
        if approver_id == state["input"]["requester_id"]:
            self.audit.log(rid, "approve", "approval_refused",
                           {"approver": approver_id, "reason": "cannot approve own request"})
            raise ApprovalError(f"{approver_id} submitted this request and cannot approve it")
        try:
            approver = self.call_tool(rid, "lookup_user", {"user_id": approver_id}, "approve")
        except ToolFailed as e:
            raise ApprovalError(f"cannot verify approver {approver_id}: {e}") from e
        if approver.status != "active":
            self.audit.log(rid, "approve", "approval_refused",
                           {"approver": approver_id, "reason": f"approver is {approver.status}"})
            raise ApprovalError(f"approver {approver_id} is {approver.status}")

        state["approval"] = {"approver_id": approver_id, "approved": approve, "reason": reason,
                             "plan_hash": state["plan_hash"], "at": now_iso()}
        transition(state, Status.APPROVED if approve else Status.REJECTED,
                   f"{'approved' if approve else 'rejected'} by {approver_id}")
        self.store.save(state)
        self.audit.log(rid, "approve", "approved" if approve else "rejected", state["approval"])
        return state

    # ================================================================ 6-7 execute + verify
    def execute(self, rid: str, simulate_failure: str | None = None) -> dict:
        state = self._load(rid)

        # replay of a finished request -> no-op
        if state["status"] == Status.COMPLETED.value:
            self.audit.log(rid, "execute", "replay_no_op", {"result": state.get("result")})
            state["replayed"] = True
            return state

        # ---- the approval gate
        approval = state.get("approval")
        if state["status"] not in (Status.APPROVED.value, Status.FAILED_SAFE.value) \
                or not approval or not approval.get("approved"):
            self.audit.log(rid, "execute", "execute_blocked", {"reason": "no valid approval",
                                                               "status": state["status"]})
            raise ApprovalRequired(f"cannot execute {rid}: status {state['status']}, no valid approval")
        if approval["plan_hash"] != sha(state["plan"]):
            self.audit.log(rid, "execute", "execute_blocked", {"reason": "plan changed after approval"})
            raise ApprovalRequired("plan changed after approval - re-approval required")

        plan = state["plan"]
        idem_key = sha({"request_id": rid, "tool": plan["tool"], "params": plan["params"]})
        state["idempotency_key"] = idem_key

        prior = self.store.get_action(idem_key)
        if prior:
            self.audit.log(rid, "execute", "idempotent_hit", {"idempotency_key": idem_key})
            result = prior
        else:
            try:
                reasons = self._recheck_before_execute(state)
            except UnknownUser as e:                       # the user was removed meanwhile
                reasons = [str(e)]
            except (ToolFailed, ToolSchemaError, ToolNotAllowed) as e:
                state["error"] = str(e)
                transition(state, Status.FAILED_SAFE, f"re-check before execute failed, safe to retry: {e}")
                self.store.save(state)
                self.audit.log(rid, "execute", "failed_safe", {"error": str(e)})
                return state
            if reasons:
                state["decision_at_execute"] = {"verdict": "BLOCK", "block_reasons": reasons}
                transition(state, Status.BLOCKED, "rules re-checked before execute: " + "; ".join(reasons))
                self.store.save(state)
                self.audit.log(rid, "execute", "blocked_at_execute", {"block_reasons": reasons})
                return state
            self.tools.failure_mode = simulate_failure
            try:
                grant = self.call_tool(rid, plan["tool"], plan["params"], "execute")
            except (ToolFailed, ToolSchemaError, ToolNotAllowed) as e:
                state["error"] = str(e)
                transition(state, Status.FAILED_SAFE, f"execute failed, safe to retry: {e}")
                self.store.save(state)
                self.audit.log(rid, "execute", "failed_safe", {"error": str(e),
                                                               "note": "approval kept; re-run execute after fix"})
                return state
            finally:
                self.tools.failure_mode = None
            result = grant.model_dump()
            self.store.record_action(idem_key, rid, result)

        state["result"] = result
        state.pop("error", None)
        transition(state, Status.EXECUTED)
        self.store.save(state)
        self._verify(state)
        self.store.save(state)
        return state

    def _recheck_before_execute(self, state: dict) -> list[str]:
        """The world may have changed since the plan was approved: the employee may have
        been disabled, moved department, or received the same access through another
        request. Re-read the directory and apply the same rules again. Returns block reasons."""
        rid = state["request_id"]
        und = sc.Understanding.model_validate(state["understanding"])
        target = self._lookup_known_user(rid, state["input"]["target_user_id"], "target user")
        requester = target if state["input"]["requester_id"] == target.user_id else \
            self._lookup_known_user(rid, state["input"]["requester_id"], "requester")
        decision = guard.evaluate(und, requester, target, self.tools.index.by_system(und.system),
                                  self.tools.index.general(), state["injection_hits"],
                                  current_request_id=rid)
        self.audit.log(rid, "execute", "rules_rechecked", {"verdict": decision.verdict,
                                                           "block_reasons": decision.block_reasons})
        return [] if decision.verdict == "ALLOW" else (decision.block_reasons + decision.missing)

    def _verify(self, state: dict) -> None:
        """Post-Action Verification: re-read the directory, do not trust the tool's answer."""
        rid, plan = state["request_id"], state["plan"]["params"]
        user = self.call_tool(rid, "lookup_user", {"user_id": plan["user_id"]}, "verify")
        matches = [g for g in user.access if g.request_id == rid]
        checks = {
            "grant_exists": len(matches) >= 1,
            "exactly_one_grant": len(matches) == 1,
            "system_matches": bool(matches) and matches[0].system == plan["system"],
            "level_matches": bool(matches) and matches[0].access_level == plan["access_level"],
            "has_expiry": bool(matches) and matches[0].expires_at > matches[0].granted_at,
        }
        ok = all(checks.values())
        state["verification"] = {"ok": ok, "checks": checks, "at": now_iso()}
        transition(state, Status.COMPLETED if ok else Status.VERIFICATION_FAILED)
        self.audit.log(rid, "verify", "verified" if ok else "verification_failed", state["verification"])

    # ================================================================ misc
    def _load(self, rid: str) -> dict:
        state = self.store.get(rid)
        if state is None:
            raise KeyError(f"unknown request {rid}")
        return state

    def status(self, rid: str) -> dict:
        return self._load(rid)


def _short(obj, limit: int = 600):
    s = canonical(obj)
    return obj if len(s) <= limit else s[:limit] + "...(truncated)"


def reset_runtime(runtime_dir: Path | None = None, data_dir: Path | None = None) -> None:
    runtime_dir = runtime_dir or PROJECT_ROOT / "runtime"
    data_dir = data_dir or PROJECT_ROOT / "data"
    if runtime_dir.exists():
        shutil.rmtree(runtime_dir)
    runtime_dir.mkdir(parents=True)
    shutil.copy(data_dir / "directory.json", runtime_dir / "directory.json")
