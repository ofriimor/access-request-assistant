"""
Generates reports/as_built.html from REAL data:
  - all requests in the state DB (scenario results)
  - the audit log (per-request timeline)
  - pytest JUnit XML (test evidence), if runtime/test_results.xml exists
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .audit import now_iso
from .engine import PROJECT_ROOT


def read_junit(path: Path) -> dict | None:
    if not path.exists():
        return None
    root = ET.parse(path).getroot()
    suite = root if root.tag == "testsuite" else root.find("testsuite")
    cases = []
    for tc in suite.iter("testcase"):
        failed = tc.find("failure") is not None or tc.find("error") is not None
        cases.append({"name": tc.get("name"), "time": tc.get("time"), "ok": not failed})
    return {"total": len(cases), "passed": sum(c["ok"] for c in cases), "cases": cases}


def generate(engine, out: Path | None = None) -> Path:
    out = out or PROJECT_ROOT / "reports" / "as_built.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    env = Environment(loader=FileSystemLoader(Path(__file__).parent / "templates"),
                      autoescape=select_autoescape(["html", "j2"]))
    requests = engine.store.all()
    audits = {r["request_id"]: engine.audit.read(r["request_id"]) for r in requests}
    intake_rejections = [e for e in engine.audit.read() if e["request_id"] is None]
    html = env.get_template("as_built.html.j2").render(
        generated_at=now_iso(),
        llm=engine.llm.name,
        requests=requests,
        audits=audits,
        intake_rejections=intake_rejections,
        tests=read_junit(engine.runtime_dir / "test_results.xml"),
        audit_count=len(engine.audit.read()),
    )
    out.write_text(html, encoding="utf-8")
    return out
