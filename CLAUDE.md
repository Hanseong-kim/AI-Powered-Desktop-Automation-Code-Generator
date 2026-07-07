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
python agent/mock_events.py                        # expect 49/49 checks
```

---

## 2. Architecture

```
React UI (3000) --HTTP--> Express (3002) --HTTP--> Python Agent (4444)
      ^                        |
      +------- SSE feed -------+
```

`/api/generate` is template-based (no LLM call) — no API key, no `.env`, no network
call to any AI provider. The Groq/LLM-generation path described in older docs
(`Claude code task v2.md`, `Claude code task server token opt.md`) has been fully
replaced; those files are historical and superseded.

Key files:
- `server/server.js` — `/api/generate` (template-based, no LLM); saves to `generated-wdio/<AppName>/`
- `agent/agent.py` — pynput hooks → raw_queue → worker thread (UIA/COM)
- `ui/src/components/ControlPanel.jsx` — presets: Calculator/Notepad/Paint/Registry Editor/Custom
- `generated-wdio/<AppName>/wdio.conf.js` — generated per app (run with `npx wdio run <AppName>/wdio.conf.js`)

Agent two-thread rule: pynput callbacks enqueue raw dicts only (never UIA).
All UIA/COM runs on the worker thread after `CoInitialize`.

---

## 3. Hard Rules (never break)

- **pynput callbacks**: enqueue + return immediately. No UIA/COM inside hooks.
- **File naming**: PascalCase app name → subfolder name. Server enforces this.
- **No Java, no Playwright**: test-runner 삭제됨, generated-playwright 경로 삭제됨.
  WebdriverIO (JS) 템플릿 생성만 지원.
- **Regression gates**: `python agent/mock_events.py` 49/49 checks.
- **Documentation honesty**: never write GUI-unverified work as "DONE" in docs.
- **`generated-wdio/*` 직접 편집 금지**: 매 Generate 호출마다 덮어써지는 산출물이므로
  수정은 항상 `server/server.js`/`agent/agent.py` 원본에서.

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

**Since session 13 (commit `f268f4e`, 2026-07-06): FreeDM(Qt/QML) + ClaudeDesktop(Electron)
support added** — hwnd 기반 창 추적(`initAppHwnd`/`normalizeWindowSimple`), QML 셀렉터
비유일성 대응(`trustLiveSelector`), `osType`/`osClickRel` 등. 코드 레벨 검증(생성물 확인)은
됐지만 **`npx wdio run` 실제 GUI 통과 로그가 daily note에 없음** — 다음 세션에서 실행하며
`[hwnd]`/`[normalize]` 로그와 함께 결과를 daily note에 남길 것 (문서 정직성 원칙, §3).

**Next actions:**
1. FreeDM/ClaudeDesktop `npx wdio run <App>/wdio.conf.js` 실제 실행 → 통과 여부와 elapsed를 기록.
2. Run VSCode full flow with VSCode + 폴더 열기 dialog actually open → measure real timing.
3. Notepad regression: regenerate + `npx wdio run Notepad/wdio.conf.js`.
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
- **WordPad**: removed from Win11 24H2. Do not add back as preset.
- **VSCode/Electron content**: Chromium renderer exposes no UIA tree → clicks on
  VSCode's own menus/editor UI fall back to absolute `osClick(x,y)` (breaks if window
  moves). Native dialogs (e.g. "Open Folder") are safe — those use live UIA re-query.
  Untested fix candidate: launch with `--force-renderer-accessibility`.
- **QML/Qt controls (FreeDM)**: `el.click()` (UIA Invoke) can succeed with no error yet
  never reach the real MouseArea. Also `el.getRect()` is unreliable on WinAppDriver —
  use `getLocation()+getSize()`. See `trustLiveSelector`/`osClickRel` in server.js.
- **`agent.py` requires restart after edits** — no hot reload; changes only take effect
  on next `python agent/agent.py` run.
- Full history → `dev-log.md` | Issues & fixes → `troubleshooting.md`

---

## Assignment grading weights

capture 25% · element inspection 20% · generated code quality 25% ·
architecture/live feed 15% · reliability 10% · docs/demo 5%
