# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# AI-Powered Desktop Automation Code Generator

Records user interactions with any Windows desktop app and generates runnable
Appium Java (TestNG) test code via Groq AI. Three cooperating processes.

## Architecture

```
React UI (3000) --HTTP--> Express (3002) --HTTP--> Python Agent (4444)
      ^                      |
      +---- SSE live feed ---+--> Groq API (llama-3.3-70b-versatile)
```

## Layout

- `agent/agent.py` — Python capture agent (port 4444). pynput global hooks,
  Windows UI Automation element inspection (comtypes), app filtering,
  typing buffer, double-click detection, scroll debounce. POSTs events to
  Express `/api/events`.
- `server/server.js` — Express bridge (port 3002). Proxies start/stop to the
  agent, broadcasts events over SSE (`/api/stream`), calls Groq to generate
  two Java files in parallel (`/api/generate`).
- `ui/` — React dashboard (port 3000). (Phase 4 — may not exist yet.)

## Agent internals (`agent/agent.py`)

The agent has a strict two-thread design:
- **pynput hook callbacks** — enqueue raw `{kind, x, y, ts, ...}` dicts onto `raw_queue` and return immediately. No COM/UIA calls here.
- **Worker thread** — calls `CoInitialize`, runs `UIAInspector`, drains `raw_queue`, does all UIA lookups, buffers keystrokes, debounces scrolls, detects double-clicks, then POSTs enriched events to Express.

Timing constants in `agent.py`: `DOUBLE_CLICK_INTERVAL=0.50s`, `DOUBLE_CLICK_RADIUS=6px`, `SCROLL_FLUSH_IDLE=0.40s`.

App filtering uses both `hwnd→pid` mapping (via `win32process`) and `psutil` parent-process walk to catch apps that re-spawn under a child process.

## Hard constraints — do not violate

- **Native Windows only.** Never suggest WSL, Docker, or Linux paths. The agent
  uses win32 APIs, pynput global hooks, and COM (UIAutomationCore.dll).
- **Never do UIA lookups inside pynput hook callbacks.** Hooks must only
  enqueue raw data and return immediately (OS input must never block).
  All UIA/COM work happens on the worker thread (CoInitialize there).
- **The agent must run from an Administrator terminal**, otherwise
  AutomationId/Name come back empty. The integrated VS Code terminal is NOT
  admin unless VS Code itself was launched as administrator.
- **Generated Java requirements** (graded): package `com.qaforge.tests`,
  TestNG @BeforeClass/@Test/@AfterClass, Page Object Model, WebDriverWait 15s
  (never Thread.sleep), full imports, no TODOs, println step logs, final Assert.
- **API key**: entered by the user per request, never hard-coded.
- Events with unreadable element details are still recorded (empty fields),
  never dropped or allowed to crash the agent.

## Run

```powershell
# Terminal 1 (normal)
cd server; npm install; node server.js

# Terminal 2 (ADMINISTRATOR PowerShell)
cd agent; pip install -r requirements.txt; python agent.py
# startup log must say "Administrator rights: YES"

# Smoke test
curl -X POST http://localhost:3002/api/start -H "Content-Type: application/json" -d '{"appName":"Calculator","exePath":"C:\\Windows\\System32\\calc.exe","platform":"Windows"}'
curl -X POST http://localhost:3002/api/stop
curl http://localhost:3002/api/generate ...   # needs Groq API key
```

## Server API surface (`server/server.js`)

| Endpoint | Notes |
|---|---|
| `POST /api/start` | `{ appName, exePath, platform }` → proxies to agent `/start`, resets event list |
| `POST /api/stop` | proxies to agent `/stop` |
| `GET /api/status` | returns `{ agentOnline, isAdmin, recording, eventCount }` |
| `POST /api/events` | agent POSTs each captured event here; broadcast over SSE |
| `GET /api/events` | returns full recorded event array |
| `DELETE /api/events` | clears event array |
| `GET /api/stream` | SSE endpoint — sends `snapshot` on connect, then `status`/`capture`/`generation` events |
| `POST /api/generate` | `{ apiKey, appName, platform }` → calls Groq in parallel for two strategies, returns `{ ok, files: [{filename, content}] }` |

### Code generation output
`/api/generate` always produces two `.java` files in parallel:
- `{AppName}TestById.java` — locates elements by `AutomationId` (falls back to `By.name`, then `By.className`)
- `{AppName}TestByClass.java` — locates elements by `ClassName` (disambiguates with `By.name` or XPath `@Name`)

Both files use package `com.qaforge.tests`, TestNG, Page Object Model, `WebDriverWait(15s)`.

## Known gotchas

- comtypes generates the UIAutomationCore wrapper on first run (a few seconds).
- Win11 Notepad/Calculator are UWP-ish and respawn under a different process;
  the agent tracks child processes of the launched PID, but classic Win32 apps
  give the most stable AutomationIds for demos.
- `ElementFromPoint` sometimes returns a container instead of the clicked
  control; if automationId is empty, consider a parent/child fallback
  (also counts as the "self-healing locator" bonus).
- SSE through Express: keep `flushHeaders()`, no compression middleware.

## Assignment grading weights (prioritize accordingly)

capture 25% · element inspection 20% · generated code quality 25% ·
architecture/live feed 15% · reliability 10% · docs/demo 5%