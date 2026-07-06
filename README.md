# AI-Powered Desktop Automation Code Generator

Records user interactions with any Windows desktop application (Win32, WPF,
UWP, Qt, or Electron) and generates runnable **WebdriverIO (JavaScript)** or
**Playwright (Python)** test code.

## Architecture

```
React UI (3000) --HTTP--> Express (3002) --HTTP--> Python Agent (4444)
      ^                       |
      +---- SSE live feed ----+
```

Three cooperating processes. The Python agent captures mouse/keyboard input and
Windows UI Automation element data; the Express bridge stores events, decides
the replay architecture (see [App Support Tiers](#app-support-tiers) below),
and generates the test code directly from the recorded events (template-based,
no LLM call); the React dashboard provides live monitoring and triggers code
generation.

## Prerequisites

| Tool | Version | Notes |
|---|---|---|
| Python | 3.9+ | Must run agent from **Administrator** terminal |
| Node.js | 18+ | For Express bridge, React UI, and generated WebdriverIO tests |
| WinAppDriver | 1.2.1 | Install + enable Developer Mode (Settings → Privacy & security → For developers). No manual startup needed — the generated test suite's `@wdio/appium-service` spawns Appium (which proxies to WinAppDriver) automatically. |

> There is no Java/Maven dependency, and no external API key is required —
> code generation is fully template-based (see the note in
> [Recording a Session](#2-recording-a-session)).

---

## 1. Install & Run the Code Generator

### Terminal 1 — Express Bridge (normal terminal)

```powershell
cd server
npm install
node server.js
# Listening on http://localhost:3002
```

### Terminal 2 — Python Agent (Administrator PowerShell)

```powershell
cd agent
pip install -r requirements.txt
python agent.py
# Must print: Administrator rights: YES
```

If it prints `NO`, close the terminal and reopen PowerShell using
"Run as Administrator" — without it, UIA element properties
(`automationId`/`name`) come back empty for most applications.

### Terminal 3 — React UI (normal terminal)

```powershell
cd ui
npm install
npm run dev
# Open http://localhost:3000
```

---

## 2. Recording a Session

1. Select a **Target App** from the preset dropdown — Calculator, Notepad,
   Paint (UWP), Registry Editor, IDM, VSCode, GitHub Desktop, Free Download
   Manager, Claude Desktop, or **Custom…** (enter App Name + Exe Path
   manually; UWP apps use an AUMID like `Package.Family.Name!App` instead of
   a file path — the agent detects the `!` and launches via
   `explorer shell:AppsFolder` automatically).
2. Select **Platform** (Windows / Android / iOS).
3. Select **Output Framework**: `WebdriverIO JavaScript (v9)` or
   `Playwright Python`.
4. Click **Launch** — the target app opens and recording begins.
5. Interact with the app (clicks, typing, scrolling). **English input only** —
   IME/CJK keystrokes are silently dropped. Avoid clicking the taskbar or
   other windows right after Launch, and wait for the app window to fully
   render before your first click (a click captured before the target window
   is fully resolved gets dropped rather than mis-attributed).
6. Click **Stop** when done.
7. Each captured event row can be **deleted individually** by hovering over
   it and clicking the `×` button.
8. Click **Generate Code**. Generated files are **saved to disk
   automatically** (toast confirms the path) under `generated-wdio/<AppName>/`
   (PascalCase folder name) or `generated-playwright/`.

### Generated output

| Framework | Files |
|---|---|
| WebdriverIO | `generated-wdio/<AppName>/{App}TestById.js`, `{App}TestByClass.js`, `wdio.conf.js`, plus `osClick.ps1`/`osScroll.ps1`/`osType.ps1`/`osWindowRect.ps1`/`osMoveWindow.ps1` replay helpers |
| Playwright Python | `generated-playwright/test_{app}_playwright.py` |

> **Generation is template-based, not an LLM call.** `/api/generate` in
> `server/server.js` builds WebdriverIO/Playwright code directly from the
> recorded event list — no external API, no rate limits, no key required.
> The UI still has a "Groq API Key" field and the server still loads
> `GROQ_API_KEY` from `.env` if present, but neither is currently read by
> the generation endpoint — they're inert leftovers from an earlier
> LLM-based version of the generator. The two WebdriverIO files (ById +
> ByClass) are generated together and typically finish in well under a
> second.

---

## 3. Run Generated WebdriverIO Tests

```powershell
cd generated-wdio
npm install          # first time only — installs webdriverio, @wdio/appium-service, appium, etc.
npx wdio run <AppName>/wdio.conf.js
# e.g. npx wdio run Calculator/wdio.conf.js
```

Appium starts automatically (via `@wdio/appium-service`) on port `4723` and
proxies to WinAppDriver — no separate WinAppDriver terminal needed.

To run only one spec file:
```powershell
npx wdio run <AppName>/wdio.conf.js --spec ./<AppName>/{App}TestById.js
```

### Run Generated Playwright Tests

```powershell
cd generated-playwright
pip install playwright
playwright install
python test_{app}_playwright.py
```

---

## 4. Regression Testing

```powershell
# Requires only the server running (no agent, no admin rights needed)
cd server; node server.js         # Terminal 1
python agent/mock_events.py       # Terminal 2
# Prints "N/N checks passed"
```

`mock_events.py` also has a handful of checks that only run when
`GROQ_API_KEY` is set in the environment (`$env:GROQ_API_KEY="gsk_..."; python
agent/mock_events.py`) — these are legacy checks against the same inert code
path described above and can be skipped safely.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `Agent unreachable` from Express | Python agent not running | Start `python agent.py` in Admin terminal |
| `Administrator rights: NO` | Agent not started as Administrator | Reopen PowerShell → Run as Administrator |
| UIA properties (`automationId`/`name`) empty | Not running as Admin | Restart agent terminal as Administrator |
| `Connection refused` on port 4723 | Appium/WinAppDriver not started | Handled automatically by `@wdio/appium-service` when you run `npx wdio run ...`; check the console for `Appium started with ID: ...` |
| `SessionNotCreatedException` | Wrong exe path / AUMID in preset | Verify the path/AUMID passed to Launch |
| `NoSuchElementException` | Locator mismatch or timing | Try the `ByClass` file instead of `ById` |
| A click in the generated test lands on the wrong element/coordinate | Electron app's dynamic content (chat lists, scroll position) differs between recording and replay | Coordinate-only replay (Tier 3, see below) is inherently sensitive to app state drift — re-record close to the replay environment |
| Recorded events include clicks on unrelated windows (taskbar, IDE, task switcher) | Clicked outside the target window during/right after recording | Delete the stray event rows before generating, or re-record avoiding other windows for the first few seconds |
| Toast "Server connection lost" loops | Express server crashed | Restart `node server.js`; toasts fire once per episode |
| `UnicodeEncodeError cp949` | Windows terminal encoding | Use `chcp 65001` before running Python scripts |

---

## App Support Tiers

The automation engine uses a three-tier strategy based on how much the app
exposes via Windows UI Automation (UIA), decided per-recording by
`needsSessionSwitching()` in `server/server.js`:

| Tier | App Types | Detection | Replay Strategy |
|------|-----------|-----------|------------------|
| 1 — Win32/WPF/UWP | Calculator, Notepad, Registry Editor, IDM | Full UIA tree, single window | `browser.$(selector)` (automationId → name → className), falls back to `getCenterSimple()` (live `getRect()`) + OS-level `osClick.ps1` click if the selector fails |
| 2 — Qt | Free Download Manager | Partial UIA tree; containers can swallow AutomationId (QML sets it on every node, not just leaves) | Same selector-first approach as Tier 1; `UIAInspector._deepen()` walks past over-matched containers using bounding-rect/ControlType heuristics |
| 3 — Electron / multi-window | VSCode, Claude Desktop, GitHub Desktop | Window class `Chrome_WidgetWin*`, or any app that opens more than one top-level window | Session-switching architecture: `launchApp()` launches a fresh instance and waits for a real (non-zero-size) window; clicks replay via `osClickRel(titleFrag, relX, relY, ...)`, re-reading the live window rect each time so replay tracks a moved/resized window |

### Coordinate replay

Pointer events carry both `relX`/`relY` (window-relative) and absolute `x`/`y`
as a fallback. Replay is **not** done via WebdriverIO/W3C pointer Actions
(WinAppDriver doesn't support them) — it's done via direct OS-level mouse
injection (`user32.dll` `SendInput`, wrapped in `osClick.ps1`/`osScroll.ps1`),
so it also works against Electron web content where `NativeWindowHandle` is 0
and no UIA-scoped session is possible.

### Known limitations

- **Electron coordinate replay is state-sensitive.** If the app's dynamic
  content (chat history, scroll position, item order) differs between
  recording and replay, a recorded pixel offset can land on the wrong
  element — there's no selector-based verification for Electron clicks today
  (a Root-session UIA lookup is possible but costs 10–50s per click, so it's
  intentionally not used for every step).
- **Paint canvas drawing.** Toolbar/button clicks work, but freehand
  mouse-drag drawing cannot be reliably replayed.
- **Multi-monitor moves.** Coordinates are relative to the window origin, not
  the screen — moving the window to a different monitor between capture and
  replay can shift coordinates if the monitor's origin differs.
