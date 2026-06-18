# CLAUDE.md — AI-Powered Desktop Automation Code Generator

Single entry point. Read this first every session.
Details & history → `C:\hansung\note\project\code-generator\dev-log.md`

---

## 1. Commands

```powershell
# Terminal 1 — Express bridge
cd server; node server.js                          # http://localhost:3002

# Terminal 2 — Capture agent (ADMINISTRATOR PowerShell)
cd agent; python agent.py
# Must print: "Administrator rights: YES"

# Terminal 3 — React UI
cd ui; npm start                                   # http://localhost:3000

# Run generated tests (WinAppDriver must be running at 4723 first)
cd test-runner
$env:JAVA_HOME = "C:\Program Files\Eclipse Adoptium\jdk-11.0.31.11-hotspot"
mvn test

# Regression (no agent needed, server must be running)
python agent/mock_events.py                        # expect 35/35 non-Groq checks

# Compile loop (3 consecutive BUILD SUCCESS target, needs .env GROQ_API_KEY)
python agent/compile_loop.py
```

`.env` at repo root (gitignored): `GROQ_API_KEY=gsk_...`

---

## 2. Architecture

```
React UI (3000) --HTTP--> Express (3002) --HTTP--> Python Agent (4444)
      ^                        |
      +------- SSE feed -------+--> Groq API (llama-3.3-70b-versatile)
```

Key files:
- `server/server.js` — SYSTEM_PROMPT, `/api/generate` (sequential ById→ByClass, 4s gap)
- `agent/agent.py` — pynput hooks → raw_queue → worker thread (UIA/COM)
- `ui/src/components/ControlPanel.jsx` — presets: Calculator/Notepad/Paint/Registry Editor/Custom

Agent two-thread rule: pynput callbacks enqueue raw dicts only (never UIA).
All UIA/COM runs on the worker thread after `CoInitialize`.

SYSTEM_PROMPT driver init: ProcessBuilder launch → Root session → `By.name(windowTitle)`
→ `NativeWindowHandle` → `appTopLevelWindow` hex string. Covers Win32 + UWP.

---

## 3. Hard Rules (never break)

- **Groq model**: `llama-3.3-70b-versatile` always. 8b is banned (code quality = 25% grade).
- **API key**: never hardcode/print. UI uses `req.body.apiKey`; `.env` is script-only fallback.
- **pynput callbacks**: enqueue + return immediately. No UIA/COM inside hooks.
- **Java files**: `package com.qaforge.tests`, TestNG `@BeforeClass/@Test/@AfterClass`,
  Page Object Model, `WebDriverWait(15s)`, no `Thread.sleep`, no TODOs, `System.out.println` steps.
- **File naming**: public class name == filename (PascalCase). Server enforces this.
- **Regression gates**: mock_events 35/35 non-Groq checks, compile_loop 3× BUILD SUCCESS.
- **Documentation honesty**: never write GUI-unverified work as "DONE" in docs.

---

## 4. Current Status  *(update every session)*

**Verified (GUI + mvn test):**
- Notepad end-to-end: type capture → Generate → `mvn test` → BUILD SUCCESS (Tests run: 2)
- All 6 grading criteria implemented

**Code-complete, GUI not yet re-verified:**
- Calc click fix (session 11): `_is_target_pid` exe stem matching — "calc" ⊂ "calculatorapp"
  → language-independent UWP process identification. `_emit` now logs `[skip]` when filtering.
- Enter newline (session 10): `\n` appended to type buffer before flush.
- TreeWalker fallback (session 10): deepens element resolution when automationId empty.
- Non-input key filter (session 10): Button/etc. keystrokes ignored — prevents UWP synthetic keys.
- Presets: WordPad removed (Win11 24H2), Registry Editor added.

**Next actions:**
1. Restart agent (Admin) → click Calculator button → verify `#N click id='num2Button'` in log.
2. Test notepad "line1[Enter]line2" → verify two lines captured.
3. Confirm no `[skip]` log entries for target app events.
4. Check `INPUT_CONTROL_TYPES` if typing in new apps is silently dropped
   (current set: `{"Edit", "Document", "ComboBox"}`).

**Risk:** If a text area's `controlType` is not in `INPUT_CONTROL_TYPES`, typing is filtered.
Verify on each new app type.

---

## 5. Known Traps

- **UWP apps** (Win11 Calculator, Paint, Notepad): `setApp(exePath)` fails → use
  appTopLevelWindow. Clicks/focus capture unreliable. Demo on Win32 (regedit, classic Notepad).
- **By.name exact-match**: UWP window titles change on load. Use XPath `contains(@Name,...)` fallback.
- **Agent not Admin**: element `automationId`/`name` come back empty. Always verify startup log.
- **Groq 429**: account-level quota (not per-key). Wait ~35s or use `compile_loop` retry logic.
- **WordPad**: removed from Win11 24H2. Do not add back as preset.
- Full history → `dev-log.md` | Issues & fixes → `troubleshooting.md`

---

## Assignment grading weights

capture 25% · element inspection 20% · generated code quality 25% ·
architecture/live feed 15% · reliability 10% · docs/demo 5%
