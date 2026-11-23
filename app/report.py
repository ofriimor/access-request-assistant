"""
Generates reports/as_built.html from REAL data:
  - all requests in the state DB (scenario results)
  - the audit log (per-request timeline)
  - pytest JUnit XML (test evidence), if runtime/test_results.xml exists
"""
from __future__ import annotations

import base64
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .audit import now_iso
from .engine import PROJECT_ROOT

def _scenario_index() -> tuple[dict, dict]:
    """Map each request back to the scenario file it came from (data/scenarios/).
    Valid requests are matched by request ID, intake rejections by their text."""
    from pydantic import ValidationError
    from . import scenarios
    from .engine import make_request_id
    from .schemas import AccessRequestInput
    by_id, by_text = {}, {}
    for path in scenarios.all_scenarios():
        case = scenarios.load(path)
        info = {"name": scenarios.name_of(path), "description": case["description"],
                "expected": case["expect"].get("status")}
        try:
            by_id[make_request_id(AccessRequestInput.model_validate(case["request"]))] = info
        except ValidationError:
            by_text[case["request"].get("request_text", "")] = info
    return by_id, by_text


STATUS_HE = {
    "COMPLETED": "הושלם ואומת", "BLOCKED": "נחסם", "NEEDS_INFO": "חסר מידע",
    "REJECTED": "נדחה ע״י המנהל", "FAILED_SAFE": "נעצר בבטחה", "INVALID": "נדחה בקליטה",
    "AWAITING_APPROVAL": "ממתין לאישור", "APPROVED": "אושר, טרם בוצע",
    "VERIFICATION_FAILED": "האימות נכשל", "EXECUTED": "בוצע, טרם אומת",
}
STATUS_TONE = {
    "COMPLETED": "ok", "BLOCKED": "stop", "REJECTED": "stop", "INVALID": "stop",
    "VERIFICATION_FAILED": "stop", "NEEDS_INFO": "wait", "FAILED_SAFE": "wait",
    "AWAITING_APPROVAL": "wait", "APPROVED": "wait", "EXECUTED": "wait",
}

BAD_EVENTS = {"tool_error", "failed_safe", "execute_blocked", "validation_failed", "tool_schema_rejected",
              "tool_blocked", "approval_refused", "verification_failed", "unknown_user", "llm_output_invalid"}
GOOD_EVENTS = {"approved", "verified", "plan_created"}
NOTE_EVENTS = {"rejected", "replay_detected", "replay_no_op", "idempotent_hit"}


def read_junit(path: Path) -> dict | None:
    if not path.exists():
        return None
    root = ET.parse(path).getroot()
    suite = root if root.tag == "testsuite" else root.find("testsuite")
    cases = []
    for tc in suite.iter("testcase"):
        failed = tc.find("failure") is not None or tc.find("error") is not None
        name = tc.get("name")
        readable = name.removeprefix("test_").replace("_", " ").capitalize()
        cases.append({"name": name, "readable": readable, "time": tc.get("time"), "ok": not failed})
    return {"total": len(cases), "passed": sum(c["ok"] for c in cases), "cases": cases}


def _data_uri(path: Path) -> str:
    mime = "image/svg+xml" if path.suffix == ".svg" else "image/png"
    return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode()


def _event_tone(event: str) -> str:
    if event in BAD_EVENTS:
        return "bad"
    if event in GOOD_EVENTS:
        return "good"
    if event in NOTE_EVENTS:
        return "note"
    return ""


def _local_time(ts: str) -> str:
    try:
        return datetime.fromisoformat(ts).astimezone().strftime("%H:%M:%S")
    except ValueError:
        return ts[11:19]


def _decision_status(r: dict) -> str:
    """The decision the request reached before any human action."""
    return "AWAITING_APPROVAL" if r.get("plan") else r["status"]


def _view(r: dict, audit: list[dict], scenario: dict | None) -> dict:
    """Everything the template needs for one request, already decided here."""
    events = {e["event"] for e in audit}
    ref = r["input"].get("external_ref") or ""
    decision = _decision_status(r)
    return {
        **r,
        "ref": ref,
        "scenario": scenario["description"] if scenario else "בקשה אינטראקטיבית",
        "scenario_name": scenario["name"] if scenario else "",
        "expected": scenario["expected"] if scenario else None,
        "decision_status": decision,
        "match": (scenario["expected"] == decision) if scenario else None,
        "status_he": STATUS_HE.get(r["status"], r["status"]),
        "tone": STATUS_TONE.get(r["status"], "wait"),
        "replayed": bool(events & {"replay_detected", "replay_no_op"}),
        "blocked_before_approval": "execute_blocked" in events,
        "tool_errors": sum(1 for e in audit if e["event"] == "tool_error"),
        "timeline": [{**e, "tone": _event_tone(e["event"]), "time": _local_time(e["ts"])} for e in audit],
    }


def generate(engine, out: Path | None = None) -> Path:
    out = out or PROJECT_ROOT / "reports" / "as_built.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    env = Environment(loader=FileSystemLoader(Path(__file__).parent / "templates"),
                      autoescape=select_autoescape(["html", "j2"]))

    by_id, by_text = _scenario_index()
    requests = [_view(r, engine.audit.read(r["request_id"]), by_id.get(r["request_id"]))
                for r in engine.store.all()]
    intake_rejections = []
    for e in engine.audit.read():
        if e["request_id"] is None:
            sc = by_text.get((e["data"].get("input") or {}).get("request_text", ""))
            intake_rejections.append({**e, "scenario": sc,
                                      "match": (sc["expected"] == "INPUT_REJECTED") if sc else None})
    checked = [x["match"] for x in requests + intake_rejections if x["match"] is not None]

    assets = PROJECT_ROOT / "assets"
    logo = _data_uri(assets / "logo-bynet.png") if (assets / "logo-bynet.png").exists() else None
    client_logo = next((_data_uri(assets / n) for n in ("client-logo.svg", "client-logo.png")
                        if (assets / n).exists()), None)

    html = env.get_template("as_built.html.j2").render(
        logo=logo,
        client_logo=client_logo,
        generated_at=datetime.now().strftime("%d.%m.%Y, %H:%M"),
        generated_iso=now_iso(),
        llm=engine.llm.name,
        llm_model=getattr(engine.llm, "model", None),
        requests=requests,
        intake_rejections=intake_rejections,
        tests=read_junit(engine.runtime_dir / "test_results.xml"),
        audit_count=len(engine.audit.read()),
        completed=sum(1 for r in requests if r["status"] == "COMPLETED"),
        stopped=sum(1 for r in requests if r["tone"] == "stop") + len(intake_rejections),
        scenarios_checked=len(checked),
        scenarios_matched=sum(checked),
    )
    out.write_text(html, encoding="utf-8")
    return out
