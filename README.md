# Access Request Assistant

**Use Case 3 · Access Request Assistant.** בדיקת בקשת הרשאה מול Directory ו־Policy, הערכת סיכון וביצוע לאחר Human Approval.

המערכת מקבלת בקשת הרשאה בטקסט חופשי ומבצעת את השלבים הבאים:

1. מחלצת ממנה שדות בעזרת LLM.
2. מאתרת את ה־Policy המתאים ב־Knowledge Base ובודקת את המשתמש ב־Directory.
3. מחליטה בקוד דטרמיניסטי אם מותר לבצע.
4. עוצרת לאישור של המנהל הישיר.
5. אחרי האישור כותבת את ההרשאה ל־Directory, מאמתת אותה ומפיקה דוח As-Built.

> **עקרון מנחה:** ה־LLM מציע, הקוד מחליט.

---

## דרישות מקדימות
- Python 3.10 ומעלה
- `pip install -r requirements.txt` (מתקין pydantic, jinja2 ו־pytest)
- **אופציונלי:** מפתח API ל־LLM אמיתי. בלי מפתח המערכת עובדת עם `MockLLM` דטרמיניסטי.

```bash
# Windows PowerShell:  $env:ANTHROPIC_API_KEY="sk-..."
export ANTHROPIC_API_KEY=sk-...          # או OPENAI_API_KEY=sk-...
export LLM_MODEL=claude-sonnet-5-5       # אופציונלי (ברירת מחדל). חלופה זולה: claude-haiku-5-5
export LLM_PROVIDER=mock                 # לכפות Mock (למשל בדמו בלי אינטרנט)
```

## הרצה בפקודה אחת
```bash
python demo.py            # בדיקות, כל התרחישים ודוח As-Built
python demo.py --pause    # עצירה בין התרחישים, לדמו חי
```
התוצרים:
- `reports/as_built.html`: מסמך המסירה
- `runtime/audit.jsonl`: ה־Audit Trail
- `runtime/test_results.xml`: עדות הבדיקות

## הרצה ידנית, שלב אחרי שלב (CLI)
```bash
python -m app reset
python -m app submit data/requests/01_happy_path.json      # נעצר ב-AWAITING_APPROVAL
python -m app execute REQ-xxxxxxxxxx                       # נחסם: אין אישור
python -m app approve REQ-xxxxxxxxxx --by u1001            # המנהלת דנה מאשרת
python -m app execute REQ-xxxxxxxxxx                       # ביצוע ואימות → COMPLETED
python -m app execute REQ-xxxxxxxxxx --simulate-failure timeout   # (בבקשה אחרת) כשל Tool
python -m app reject  REQ-xxxxxxxxxx --by u1012 --reason "..."
python -m app status  REQ-xxxxxxxxxx
python -m app audit   REQ-xxxxxxxxxx
python -m app report
```

## בדיקות
```bash
python -m pytest -v
```
יש 20 בדיקות: Validation, Knowledge Match, חסימת Execute ללא אישור, אישור רק ע"י המנהל הישיר, שינוי התוכנית אחרי אישור, Missing Info, חסימה לפי Policy, Timeout עם Retry ו־Fail-safe, Recovery, פלט Tool פגום, Replay ו־Idempotency, Prompt Injection, Parameter Injection מ־LLM, ו־Tool שלא נמצא ב־Allowlist.

## תרחישי הדגמה (`data/requests/`)

| קובץ | תרחיש | תוצאה צפויה |
|---|---|---|
| `01_happy_path.json` | נועה (Sales) מבקשת CRM write | AWAITING_APPROVAL → APPROVED → COMPLETED |
| `02_missing_info.json` | "I need access to the system" | NEEDS_INFO |
| `03_blocked_admin.json` | admin על prod_db | BLOCKED (POL-GEN-00 §1) |
| `04_prompt_injection.json` | "IGNORE ALL PREVIOUS INSTRUCTIONS…" | BLOCKED (§7) |
| `05_rejected_by_manager.json` | המנהל דוחה | REJECTED, ו־Execute נחסם |
| `06_tool_failure.json` | Directory timeout | FAILED_SAFE, ואחרי הרצה חוזרת COMPLETED |
| `07_invalid_input.json` | חסר requester_id | INPUT REJECTED, לא נוצרת רשומה |

**Sample Data:**
- `data/directory.json`: 12 משתמשים, מחלקות ומנהלים, כולל משתמש disabled.
- `data/policies/`: 5 Policies.

## מבנה הפרויקט
```
app/schemas.py   חוזי נתונים (Pydantic)
app/llm.py       שלב Understand: Anthropic / OpenAI / Mock
app/tools.py     4 Tools + חיפוש ב-KB + failure injection
app/guard.py     החלטה דטרמיניסטית + זיהוי injection
app/state.py     State Machine + SQLite + טבלת idempotency
app/engine.py    Orchestrator: 7 שלבים, approval gate, retry, verify
app/audit.py     Audit log (JSONL)
app/report.py    יצירת As-Built HTML
demo.py          כל התרחישים בפקודה אחת
tests/           בדיקות אוטומטיות
docs/LEARNING_GUIDE.md   הסבר מושגים וקוד
```

## הנחות
- חבילת ההתחלה עם ה־Sample Data לא התקבלה, ולכן נתוני הדוגמה ב־`data/` נבנו במיוחד לפרויקט. הם מכסים את כל תרחישי החובה. החלפה לנתונים אחרים דורשת רק החלפת הקבצים ב־`data/`, בלי שינוי קוד, כל עוד הם באותו מבנה.
- הבקשה מוגשת ע"י העובד עצמו או ע"י המנהל הישיר שלו. המאשר הוא תמיד המנהל הישיר.
- כל הרשאה מוגבלת בזמן: ברירת מחדל לפי ה־Policy, ותקרה לפי המקסימום שנקבע בו.
- הרשאת admin לעולם לא ניתנת דרך המערכת ומופנית ל־Security.
- ה־Directory וה־KB הם קבצים מקומיים שמדמים מערכות אמיתיות (IdP ו־ITSM).

## כלים ששימשו בפיתוח
- **Python, Pydantic, Jinja2, pytest, SQLite** (ספריית התקן).
- **LLM בזמן ריצה:** Anthropic Claude או OpenAI (לבחירה), או Mock.
- **כלי AI לפיתוח:** Claude (Anthropic) שימש ליצירת שלד הקוד, הבדיקות והתיעוד. עברתי על כל רכיב, הרצתי והתאמתי. *(עדכני את הסעיף לפי מה שעשית בפועל.)*
