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

# Run generated tests (Appium auto-starts, WinAppDriver at 4723 not needed separately)
cd generated-wdio
npx wdio run Calculator/wdio.conf.js               # 폴더 이름 = PascalCase 앱 이름
npx wdio run Notepad/wdio.conf.js

# Regression (no agent needed, server must be running)
python agent/mock_events.py                        # expect 35/35 non-Groq checks
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
- `server/server.js` — `/api/generate` (template-based, no LLM); saves to `generated-wdio/<AppName>/`
- `agent/agent.py` — pynput hooks → raw_queue → worker thread (UIA/COM)
- `ui/src/components/ControlPanel.jsx` — presets: Calculator/Notepad/Paint/Registry Editor/Custom
- `generated-wdio/<AppName>/wdio.conf.js` — generated per app (run with `npx wdio run <AppName>/wdio.conf.js`)

Agent two-thread rule: pynput callbacks enqueue raw dicts only (never UIA).
All UIA/COM runs on the worker thread after `CoInitialize`.

---

## 3. Hard Rules (never break)

- **Groq model**: `llama-3.3-70b-versatile` always. 8b is banned (code quality = 25% grade).
- **API key**: never hardcode/print. UI uses `req.body.apiKey`; `.env` is script-only fallback.
- **pynput callbacks**: enqueue + return immediately. No UIA/COM inside hooks.
- **File naming**: PascalCase app name → subfolder name. Server enforces this.
- **No Java**: test-runner 삭제됨. Java/TestNG 코드 생성 없음. WdIO + Playwright only.
- **Regression gates**: mock_events 35/35 non-Groq checks.
- **Documentation honesty**: never write GUI-unverified work as "DONE" in docs.

---

## 4. Current Status  *(update every session)*

**Verified (GUI + npx wdio) — session 13:**

| App | Test | Result | Elapsed |
|-----|------|--------|---------|
| Calculator | `npx wdio run Calculator/wdio.conf.js` | **2 passed** | 46s (36s test) |
| VSCode (mock) | `npx wdio run VSCode/wdio.conf.js` | **1 passed** | 288s* |

*VSCode 288s is mock-only: native dialog not open → Root scan × 4 = ~240s timeout overhead.
In real use (dialog open), Root scan finds hwnd in one query (~5-30s), then cached for remaining steps.

**Architecture change (session 13):**
- `needsSessionSwitching(events)`: single-window non-Electron → `appium:app: exePath` (simple); multi-window or Electron → `appium:app: 'Root'` + `getWindowSession` + `getCenter` + `osClick`
- `afterAll()` (not `after()`) — Jasmine does not define `after()`, caused SKIPPED (0 its registered)
- Electron events bypass `getWindowSession` — use captured `osClick(x,y)` directly (hwnd=0 for web content)
- test-runner 폴더 삭제 (Java/TestNG 완전 제거)
- Calculator preset exePath → AUMID로 수정

**Next actions:**
1. Run VSCode full flow with VSCode + 폴더 열기 dialog actually open → measure real timing.
2. Notepad regression: regenerate + `npx wdio run Notepad/wdio.conf.js`.
3. Check `INPUT_CONTROL_TYPES` if typing in new apps is silently dropped
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
