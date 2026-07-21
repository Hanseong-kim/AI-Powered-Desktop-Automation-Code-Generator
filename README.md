# AI-Powered Desktop Automation Code Generator

Records user interactions (clicks, typing, double-clicks, scrolls) with any
Windows desktop application and generates runnable **WebdriverIO (JavaScript)**
test code that replays the session — targeting every element through
**UI Automation selectors (AutomationId / ClassName / Name XPath) only, never
screen coordinates**.

- **Generic**: point it at any `.exe` (or UWP AUMID) — no per-app integration.
- **XPath-only replay**: coordinates are forbidden everywhere. Elements without
  a unique id/name are resolved through an anchor-relative XPath
  (`//*[@AutomationId="X"]/Button[3]`). Events with no usable selector are
  generated as **explicit failing steps** instead of silently degrading.
- **Template-based generation**: no LLM call, no API key, no network — code is
  built directly from the recorded event list in well under a second.
- **Self-recovering replay**: every step is wrapped in a Fail-and-Recover
  routine that dismisses unexpected popups (e.g. "file already exists") and
  retries once before failing honestly.

## Verified Targets

GUI-confirmed with `node <AppName>TestById.js` — a standalone script, **no
WebdriverIO test runner, no `wdio.conf.js`, no `describe`/`it`/`browser`
required** (see §3 below for why this matters):

| App | Type | Notes |
|---|---|---|
| Calculator | UWP | simple mode |
| Notepad | UWP | simple mode |
| PuTTY | native Win32 dialog | category tree nav, ComboBox dropdowns (same-window and cross-window popup), tree +/- toggle, proxy radio buttons |
| FileZilla | native Win32, multi-window | folder tree nav, menu bar navigation via ExpandCollapsePattern, Site Manager dialog (separate HWND session) |
| 7-Zip | native Win32 | file list navigation, double-click into folders |

Other presets in the UI (Paint, Registry Editor, IDM, VSCode, GitHub Desktop,
Free Download Manager, Claude Desktop) are wired up but not currently
GUI-verified end to end — see **Known Limitations** below for the specific
app classes (Electron/Chromium, QML) that are out of scope for now.

## Architecture

```
React UI (3000) --HTTP--> Express (3002) --HTTP--> Python Agent (4444)
      ^                       |
      +---- SSE live feed ----+
```

Three cooperating processes:

| Process | Role |
|---|---|
| **Python agent** (`agent/agent.py`) | Global mouse/keyboard hooks (pynput) + Windows UI Automation (UIA/COM) element inspection. Hooks only enqueue raw events; all UIA work runs on a dedicated worker thread. |
| **Express bridge** (`server/server.js`) | Stores events, decides the replay architecture (single-window vs multi-window session mode), and generates the test code from templates via `/api/generate`. |
| **React dashboard** (`ui/`) | Live event feed (SSE), per-event delete, app presets, Generate button. |

## Prerequisites

| Tool | Version | Notes |
|---|---|---|
| Python | 3.9+ | The agent **must** run from an Administrator terminal |
| Node.js | 18+ | For the Express bridge, React UI, and generated tests |
| WinAppDriver | 1.2.1 | Install it and enable Developer Mode (Settings → Privacy & security → For developers). No manual startup — each generated script's own `ensureAppium()` spawns Appium, which proxies to WinAppDriver. |

> No Java/Maven, no Playwright, no API key. Output is WebdriverIO JavaScript
> only.

---

## 1. Install & Run

### Terminal 1 — Express bridge (normal terminal)

```powershell
cd server
npm install          # first time only
node server.js
# Listening on http://localhost:3002
```

### Terminal 2 — Python agent (Administrator PowerShell)

```powershell
cd agent
pip install -r requirements.txt   # first time only
python agent.py
# Must print: Administrator rights: YES
```

If it prints `NO`, close the terminal and reopen PowerShell with
"Run as Administrator" — without admin rights, UIA element properties
(`automationId`/`name`) come back **empty** for most applications and the
generated test will be full of unusable steps.

> The agent has no hot reload — after any edit to `agent.py`, restart it.

### Terminal 3 — React UI (normal terminal)

```powershell
cd ui
npm install          # first time only
npm run dev
# Open http://localhost:3000
```

---

## 2. Recording a Session

1. **Pick a Target App** from the preset dropdown (Calculator, Notepad,
   Registry Editor, …) or choose **Custom…** and enter:
   - **App Name** — becomes the PascalCase output folder name
     (e.g. `My App` → `generated-wdio/MyApp/`).
   - **Exe Path** — full path to the executable
     (e.g. `C:\Program Files\7-Zip\7zFM.exe`).
     UWP apps use an AUMID like `Package.Family.Name!App` instead of a file
     path — the agent detects the `!` and launches via
     `explorer shell:AppsFolder` automatically.
2. Click **Launch** — the target app opens and recording begins. Wait for the
   window to fully render before your first click.
3. **Interact with the app.** Supported event scope: **Click, Type,
   Double-Click, Scroll**. (Drag and right-click are captured for diagnostics
   but rendered as scope-out comments, not replayed.)
   - **English input only** — IME/CJK keystrokes are silently dropped.
   - Avoid clicking the taskbar or unrelated windows mid-recording.
   - Avoid **rapid-fire menu clicking** (opening several menus within a
     second): a menu's light-dismiss overlay can race the element inspection.
     The agent re-resolves the element beneath the overlay automatically, but
     a deliberate pace gives the cleanest capture.
4. Watch the **live event feed** — each row shows the action, the resolved
   element (automationId / name / className), and the window. If a row shows
   an empty element, that step will be generated as an explicit FAIL step
   (coordinates are never used as a fallback), so consider re-doing that
   interaction.
5. Click **Stop** when done.
6. **Delete stray rows** (mis-clicks, taskbar clicks) by hovering a row and
   clicking `×`.
7. Click **Generate Code**. Files are saved automatically under
   `generated-wdio/<AppName>/` — a toast confirms the path.

Recordings are also backed up as JSON under `recorded-events/` (git-ignored),
and can be restored via `POST /api/events/restore` for re-generation without
re-recording.

### Generated output

```
generated-wdio/<AppName>/
├── <AppName>TestById.js       # selectors prefer AutomationId (~id / XPath)
├── <AppName>TestByClass.js    # selectors prefer ClassName+Name XPath
├── wdio.conf.js               # per-app config (Appium service, capabilities)
├── osScroll.py                # UIA ScrollPattern scroll (PostMessage wheel fallback)
├── osScopedInvoke.py          # click an item that opened in a SEPARATE top-level
│                              #   window (native ComboBox dropdown / menu popup) —
│                              #   the WinAppDriver session can't see it, so this
│                              #   goes straight through COM UIA instead
├── osExpandCollapse.py        # ExpandCollapsePattern replay (ComboBox dropdowns,
│                              #   menu bar items, tree +/- toggles) — plain
│                              #   click()/InvokePattern doesn't open these
├── osType.ps1                 # OS-level SendKeys fallback for stubborn edit controls
├── osActivate.ps1             # bring the app window to the foreground
├── osWindowRect.ps1           # read window geometry (hwnd-first)
├── osMoveWindow.ps1           # restore the recorded window position/size
├── osDismissPopup.ps1         # Fail-and-Recover: dismiss unexpected dialogs
└── osEscape.ps1               # Fail-and-Recover: ESC out of stuck input states
```

The two test files are alternative locator strategies for the same recording —
if `ById` fails on an app whose ids are unstable, try `ByClass`.

> None of these helpers do coordinate injection: they handle keyboard input,
> window management, popup recovery, and pattern-based (Expand/Scroll/Invoke)
> element interaction — always selector-based, never screen pixels. The `.py`
> helpers use **COM IUIAutomation (comtypes)**, the same stack as
> `agent/agent.py`; earlier `.ps1` versions of `osScroll`/`osScopedInvoke`/
> `osExpandCollapse` used .NET managed UIA (`System.Windows.Automation`), which
> cannot see legacy Win32 controls (list rows, toolbar buttons, `SysTreeView32`
> tree items) and were replaced for exactly that reason. Do not edit generated
> files — they are overwritten on every Generate; fix `server/server.js`
> templates instead.

---

## 3. Running Generated Tests

Each generated `*TestById.js` / `*TestByClass.js` is a **standalone Node.js
script** — it does not use `describe`/`it`/`browser`/`expect`, does not read
`wdio.conf.js`, and needs no test runner:

```powershell
cd generated-wdio/<AppName>
npm install          # first time only (installs the app's own package.json)
node <AppName>TestById.js
# e.g. node CalculatorTestById.js
```

The script itself spawns Appium (`ensureAppium()`), creates the WinAppDriver
session, replays every recorded step, and exits with a non-zero
`process.exitCode` on failure — no separate Appium terminal, no
`@wdio/appium-service`, no WDIO config to keep in sync. The replay is
**visual**: the app launches, its window is moved back to the recorded
geometry, and each step clicks/types/scrolls the real UI in order, printed as
`[STEP] n:action label` as it happens.

> `wdio.conf.js` is still generated alongside the standalone scripts as a
> legacy artifact, but `npx wdio run` is no longer the supported way to run
> a test — use `node <file>.js` above.

Run the alternative locator strategy the same way:

```powershell
node <AppName>TestByClass.js
```

### How a test decides PASS/FAIL

- Every step is logged as `[STEP] n:action label`.
- Injection failures, un-resolvable selectors, and window-management errors
  are pushed into a `_failures` array; at the end, a non-empty array logs
  `[FAIL]` and sets `process.exitCode = 1` — so **any silently broken step
  fails the run** (and the process's own exit code), there are no false
  PASSes. `[PASS] all steps completed` on stdout means a clean run.
- Recoverable incidents (a popup was dismissed and the step retried
  successfully) are recorded in `_warnings` and printed, but do not fail the
  test.

### Replay architecture (chosen automatically at generate time)

| Mode | When | How it replays |
|---|---|---|
| **Simple** | Single-window native app | `appium:app = exePath`; clicks via raw Appium REST (`element` + `element/click`, UIA Invoke) |
| **Session** | Multi-window flows or Electron-class apps | `appium:app = 'Root'`; each new HWND gets its own scoped WinAppDriver session; clicks/typing resolve the XPath **inside that window's session** (`_clickScoped`/`_typeScoped`), with an explicit `switch to window: ...` step logged at every HWND boundary |

Scrolling never uses pixels: the recorded scroll container is re-found via UIA
and scrolled with `ScrollPattern.Scroll()`; legacy controls that lack
ScrollPattern get an hwnd-scoped `WM_MOUSEWHEEL` via `PostMessageW`.

**Native ComboBox/menu popups** (Win32 dropdowns, menu bar items) often render
in a **separate top-level window** rather than inside the app's main window —
a plain WinAppDriver session, scoped to the window it was created against,
can't see that popup at all. Two codegen-time mechanisms handle this
(both replay via COM UIA directly, bypassing the WinAppDriver session):

- **`osExpandCollapse`** — for controls exposing `ExpandCollapsePattern`
  (ComboBox, menu bar `MenuItem`, tree `+`/`-` toggles): expands the control,
  then searches for the target item first in the main window, then in any
  newly-appeared top-level window (native `TrackPopupMenu`-style popups).
- **`osScopedInvoke`** — for a plain `Button` (no ExpandCollapsePattern) that
  opens a dropdown list rendered as its own top-level window: the trigger
  click and the subsequent item search run **in one process**, so there's no
  gap in which the dropdown can auto-close before the item is found.

Recording captures the click(s) that open + select from these controls as
separate events; `server/server.js` merges them at codegen time into a single
call so the open→search happens without a step boundary in between.

---

## 4. Regression Testing (no agent, no admin, no GUI)

```powershell
# Terminal 1
cd server; node server.js

# Terminal 2
python agent/mock_events.py
# expect: NNN/NNN checks passed (count grows as new bugs get regression
# coverage — check the printed total, don't hardcode a number)
```

`mock_events.py` POSTs synthetic recordings to the live server — including a
simple single-window app, a multi-window (session-mode) app, and a native
Win32 dialog scenario exercising numeric-AutomationId handling, ExpandCollapse
merging, and cross-window scoped invoke — generates code for every path, and
asserts on the output: XPath-only invariants (no `osClick(`/`osDrag(`/
`osClickRel(` anywhere), anchor-XPath rendering, double-click dedupe,
Fail-and-Recover wiring (including that ESC recovery never fires against the
app's own foreground main window), helper file contents (ScrollPattern
present, `SetCursorPos` absent, COM `comtypes` used instead of managed UIA),
and that stale coordinate/managed-UIA helpers left by older versions are
removed from the output folder on regenerate. The two mock apps it generates
into (`generated-wdio/MockMulti/`, `generated-wdio/MockNative/`) are
regression-gate fixtures, not real recordings — they're git-ignored and safe
to delete; re-running the gate recreates them.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `Agent unreachable` from Express | Python agent not running | Start `python agent.py` in an Admin terminal |
| `Administrator rights: NO` | Agent not elevated | Reopen PowerShell → Run as Administrator |
| Captured elements have empty `automationId`/`name` | Agent not elevated, or the app genuinely exposes nothing (see limitations) | Restart agent as Admin; check the agent log for `[inspect] anchor XPath ...` lines |
| Test fails with `n:click:no-selector` | That event was captured with no selector and no anchor (coordinates are forbidden) | Delete the event row and re-record that interaction at a calmer pace |
| `Connection refused` on port 4723 | Appium not up | Handled by the script's own `ensureAppium()`; check the console for `[appium] starting Appium...` |
| `SessionNotCreatedException` | Wrong exe path / AUMID | Verify the path passed to Launch |
| `NoSuchElementException` at replay | Locator mismatch or timing | Try the `ByClass` spec instead of `ById`; check the app's UI state matches recording |
| Replay clicks something, a stray dialog appears, test still passes with `popup-dismissed` warning | Working as intended — Fail-and-Recover dismissed it and retried | Nothing to do; check `_warnings` output if curious |
| Recorded rows include taskbar/IDE clicks | Clicked outside the target during recording | Delete those rows before Generate |
| `UnicodeEncodeError cp949` | Windows terminal encoding | `chcp 65001` before running Python scripts |

---

## Known Limitations

- **Electron/Chromium apps (VSCode-class) are out of scope.** The Chromium
  renderer exposes no usable UIA tree, so content clicks have no selector and
  generate as explicit FAIL steps. Native OS dialogs opened by such apps
  (e.g. "Open Folder") replay fine.
- **Qt/QML apps** can accept a UIA Invoke without error while the real
  `MouseArea` never fires, and their AutomationIds are often non-unique —
  currently out of scope.
- **UWP quirks**: window titles change during load (`contains(@Name,...)`
  matching handles most of it); Win11 Notepad is a single-instance app that
  holds the user's unsaved tabs — prefer other demo targets.
- **Admin-manifested targets** (e.g. `regedit.exe`): a non-elevated agent can
  see the top-level window but UIPI blocks child element inspection — run the
  agent elevated (required anyway).
- **Typing capture** is filtered by control type
  (`{"Edit", "Document", "ComboBox"}`). If typing into a new app type is
  silently dropped, the target control's type needs to be added in
  `agent/agent.py` (`INPUT_CONTROL_TYPES`).
- **Numeric AutomationIds can be non-unique in classic Win32 dialogs.** Some
  apps (e.g. PuTTY's category panels) reuse the same numeric resource ID
  across multiple controls in different panels. Selector resolution tries an
  AND of every captured field (id + name + className) before falling back to
  a single field, to avoid matching the wrong same-id control.
- **A Korean-Windows titlebar's Close (X) button is a UIA `Button` named
  "닫기"** — the same accessible name a Win32 ComboBox dropdown arrow can
  carry. A dropdown-arrow element is always resolved by its AutomationId
  (`~DropDown`), never by a bare `//Button[@Name="닫기"]`, so it can never
  accidentally match window chrome.
- **.NET managed UIA (`System.Windows.Automation`) cannot see legacy Win32
  controls** (list rows, toolbar buttons, `SysTreeView32` tree items) — every
  replay helper that needs to reach those controls uses COM `IUIAutomation`
  (comtypes) instead, matching the stack `agent/agent.py` already uses.

## Project Layout

```
agent/          Python capture agent + mock_events.py regression gate
server/         Express bridge + template-based code generator
ui/             React dashboard (Vite)
generated-wdio/ Generated test suites (one folder per app) + shared npm deps
recorded-events/  JSON backups of every recording session (git-ignored)
poc/            Standalone PoCs (PowerShell + Python COM UIA) — XPath click,
                ScrollPattern, HWND scoping, ExpandCollapsePattern diagnostics
```
