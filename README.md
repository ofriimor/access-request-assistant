<p align="center"><img src="assets/logo-bynet.png" alt="Access Request Assistant for Bynet" width="640"></p>

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

## התקנה והגדרה (Gemini)

Python 3.10 ומעלה, ואז:
```powershell
python -m pip install -r requirements.txt
copy .env.example .env
```
פתחו את `.env` ב־VS Code והדביקו את המפתח:
```
GEMINI_API_KEY=המפתח-שלכם
LLM_PROVIDER=gemini
LLM_MODEL=gemini-2.5-flash-lite
```
המערכת טוענת את `.env` אוטומטית בכל הרצה, ואין צורך להגדיר משתנים בטרמינל. הקובץ מופיע ב־`.gitignore`, כך שהמפתח נשאר במחשב ולא עולה ל־GitHub. ערך שמוגדר בטרמינל (`$env:...`) גובר על הקובץ.

Gemini הוא הספק היחיד בזמן ריצה, ואין Mock או ספק חלופי. בלי מפתח תקף המערכת עוצרת עם הודעה ברורה. הבדיקות האוטומטיות משתמשות ב־FakeLLM מבודד ולא צורכות טוקנים, ולכן הן לא מוכיחות קריאה אמיתית ל־API.

## הרצה בפקודה אחת
```bash
python demo.py            # בדיקות, כל התרחישים ודוח As-Built
python demo.py --pause    # עצירה בין התרחישים, לדמו חי
python demo.py --pause --require-real-llm  # מומלץ לראיון: מסרב להציג Mock כ-LLM אמיתי
```
התוצרים:
- `reports/as_built.html`: מסמך המסירה
- `runtime/audit.jsonl`: ה־Audit Trail
- `runtime/test_results.xml`: עדות הבדיקות

## מצב אינטראקטיבי: הבקשה שלך, ההחלטה שלך
```bash
python -m app interactive
```
המערכת מציגה את רשימת העובדים. את בוחרת עובד וכותבת בקשה חופשית משלך. אם הבקשה מותרת, המערכת **עוצרת ומחכה** להחלטה של המנהל הישיר: את מקלידה מי מאשר ו־y או n. רק אחרי אישור היא מבצעת ומאמתת.

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
יש 33 בדיקות (כולל בדיקות ספק LLM וכשלי תקשורת): Validation, Knowledge Match, חסימת Execute ללא אישור, אישור רק ע"י המנהל הישיר, שינוי התוכנית אחרי אישור, Missing Info, חסימה לפי Policy, Timeout עם Retry ו־Fail-safe, Recovery, פלט Tool פגום, Replay ו־Idempotency, Prompt Injection, Parameter Injection מ־LLM, ו־Tool שלא נמצא ב־Allowlist.

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
app/config.py    טעינת .env (מפתח API והגדרות)
app/schemas.py   חוזי נתונים (Pydantic)
app/llm.py       שלב Understand: Gemini only
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

## לוגו
- `assets/logo-bynet.png`: הלוגו שמוצג בראש ה־README.
- `assets/logo.svg` ו־`assets/logo-icon.svg`: לוגו נוסף של המערכת, שמשמש בדוח ה־As-Built.
- **לוגו של לקוח (אופציונלי):** שמרו את קובץ הלוגו של הלקוח בשם `assets/client-logo.svg` (או `.png`), והוא יוצג כמו שהוא בכותרת דוח ה־As-Built, תחת הכיתוב "Prepared for".

## הנחות
- חבילת ההתחלה עם ה־Sample Data לא התקבלה, ולכן נתוני הדוגמה ב־`data/` נבנו במיוחד לפרויקט. הם מכסים את כל תרחישי החובה. החלפה לנתונים אחרים דורשת רק החלפת הקבצים ב־`data/`, בלי שינוי קוד, כל עוד הם באותו מבנה.
- הבקשה מוגשת ע"י העובד עצמו או ע"י המנהל הישיר שלו. המאשר הוא תמיד המנהל הישיר.
- כל הרשאה מוגבלת בזמן: ברירת מחדל לפי ה־Policy, ותקרה לפי המקסימום שנקבע בו.
- הרשאת admin לעולם לא ניתנת דרך המערכת ומופנית ל־Security.
- ה־Directory וה־KB הם קבצים מקומיים שמדמים מערכות אמיתיות (IdP ו־ITSM).

## כלים ששימשו בפיתוח
- **Python, Pydantic, Jinja2, pytest, SQLite** (ספריית התקן).
- **LLM בזמן ריצה:** Google Gemini API בלבד, מודל `gemini-2.5-flash-lite`.
- **כלי AI לפיתוח:** Claude (Anthropic) שימש ליצירת שלד הקוד, הבדיקות והתיעוד. עברתי על כל רכיב, הרצתי והתאמתי. *(עדכני את הסעיף לפי מה שעשית בפועל.)*

## הכנה להצגה ולמסירה
- [תרשים ארכיטקטורה ומפת רכיבים](docs/ARCHITECTURE.md)
- [Runbook לדמו של 10–15 דקות, שאלות ותוצרי מסירה](docs/INTERVIEW_RUNBOOK.md)
- `python demo.py --require-real-llm` מפיק תוצאות ריצה חדשות עם Gemini: `reports/as_built.html`, `runtime/audit.jsonl` ו־`runtime/test_results.xml`. אלה ההוכחות להציג. הקבצים הישנים שנוצרו במצב offline הוסרו מ־`evidence/`.
- בדיקות ה־pytest אינן דורשות מפתח API ואינן מוכיחות קישוריות ל־API אמיתי. כדי להוכיח זאת, צריך להריץ את הדמו עם מפתח תקף בסביבה שלך.
- מגבלות: אין SSO או אישור מנהל מאומת, אין חיבור ל־IdP אמיתי ואין הסרת הרשאות אוטומטית בתום התוקף.
