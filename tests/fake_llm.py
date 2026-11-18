"""Offline test double, never used by the running application."""
import json
import re

class FakeLLM:
    name = "test-double"
    SYSTEMS = [("finance_erp", r"finance|erp|sap|invoice"), ("prod_db", r"production|prod db|prod_db|database"), ("hr_portal", r"\bhr\b|hr portal|payroll"), ("crm", r"\bcrm\b|salesforce")]
    def complete(self, system, user):
        text = re.sub(r"</?request>", "", user).strip()
        low = text.lower()
        sys_ = next((s for s, pat in self.SYSTEMS if re.search(pat, low)), None)
        level = "admin" if "admin" in low else "write" if re.search(r"\bwrite\b|\bedit\b", low) else "read" if re.search(r"\bread\b|\bview\b", low) else None
        dur = None
        if m := re.search(r"(\d+)\s*days?", low): dur = int(m.group(1))
        elif m := re.search(r"(\d+)\s*months?", low): dur = int(m.group(1))*30
        just = None
        if m := re.search(r"because (.+?)(?:\.|$)", text, re.IGNORECASE): just = m.group(1).strip()
        gaps = ["several systems mentioned in one request"] if sum(bool(re.search(p, low)) for _,p in self.SYSTEMS)>1 else []
        intent = "grant_access" if re.search(r"access|grant|permission", low) else "other"
        return json.dumps({"intent":intent,"system":sys_,"access_level":level,"duration_days":dur,"justification":just,"gaps":gaps,"assumptions":[]})
