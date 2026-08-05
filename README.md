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
| HeidiSQL | Delphi/VCL, multi-window | owner-drawn ComboBoxEx item selection by position (network-type combo), cross-window session-manager ↔ preferences flow. The session-list tree (`TVirtualStringTree`) exposes zero UIA children and cannot be automated — see **Known Limitations**. The "더 보기" (More) overflow menu's items are captured by position but replay doesn't select them yet (parked, see below) |
| TeamViewer | WebView2 (Chromium), single window | first confirmed Electron/Chromium-class target — ID/password copy buttons, session-code input, "Join session", the two settings checkboxes ("Windows와 함께 TeamViewer 시작" / "이 장치에 Easy Access 권한 부여" — click the **text label**, not the checkbox glyph itself, which sits in an unnamed wrapper), and the native "빠른 연결 허용" dialog (email/password/cancel) all replay end to end (`[PASS] all steps completed`). **Requires the agent, Express bridge's spawned processes, and the generated test itself to all run from an elevated (Administrator) terminal** — TeamViewer runs elevated, and Windows' UIPI blocks a non-elevated automation client from seeing anything past the window shell (see **WebView2 / Electron apps** below). Running the generated test from a non-elevated terminal is the single most common cause of every step failing at once. |

Other presets in the UI (Paint, Registry Editor, IDM, VSCode, GitHub Desktop,
Free Download Manager, Claude Desktop) are wired up but not currently
GUI-verified end to end. VSCode/GitHub Desktop/Claude Desktop are the same
WebView2/Electron class as TeamViewer and are expected to work the same way,
but **only TeamViewer has actually been validated** — see **WebView2 /
Electron apps** below before assuming another Electron host "just works".

Two more presets (`PowerShell ISE`, `Everything`) were added 2026-08-05 to check
framework coverage this project hadn't tested before — WPF and WinForms,
respectively (the other two named target frameworks besides Win32/MFC).
`poc/probe_app_automatability.py` reports Tier 1 (supported) for both; neither
has a full record→replay GUI verification yet.

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
├── <AppName>TestById.js       # selectors prefer AutomationId (~id / XPath) — fully
│                              #   self-contained (see below)
├── <AppName>TestByClass.js    # selectors prefer ClassName+Name XPath — also self-contained
├── package.json                # no deps of its own (resolves ../node_modules); exists
│                                #   only for `npm run test:byid` convenience
└── appium.log                  # written on first run
```

The two test files are alternative locator strategies for the same recording —
if `ById` fails on an app whose ids are unstable, try `ByClass`.

**Each generated `.js` is fully self-contained.** It embeds the source of 9
helper scripts (`osScroll.py`, `osScopedInvoke.py`, `osExpandCollapse.py`,
`osType.ps1`, `osActivate.ps1`, `osWindowRect.ps1`, `osMoveWindow.ps1`,
`osDismissPopup.ps1`, `osEscape.ps1`) as string constants and self-extracts
each one to a per-process temp directory the first time it's needed — copying
just the single `.js` file to another machine/folder still runs (the only
external dependency left is the shared `../node_modules` Appium install, same
as needing Node.js itself installed).

For human inspection, `saveFiles()` also writes plain-text copies of those 9
helpers to `generated-wdio/_debug-helpers/<AppName>/` — **the generated `.js`
never reads from there** (it always uses its own embedded copies), so this
folder is purely for reading/debugging what the embedded scripts do and is
safe to delete.

| Helper | Purpose |
|---|---|
| `osScroll.py` | UIA ScrollPattern scroll (PostMessage wheel fallback) |
| `osScopedInvoke.py` | click an item that opened in a SEPARATE top-level window (native ComboBox dropdown / menu popup) — the WinAppDriver session can't see it, so this goes straight through COM UIA instead. Also the click path for **CheckBox controls** (verifies `ToggleState` actually changed, not just that the click didn't error — see below) and for **any `isWebContent` element** (WebView2/Chromium-hosted controls WinAppDriver's managed UIA client cannot see at all, regardless of session state) |
| `osExpandCollapse.py` | ExpandCollapsePattern replay (ComboBox dropdowns, menu bar items, tree +/- toggles) — plain click()/InvokePattern doesn't open these |
| `osType.ps1` | OS-level SendKeys fallback for stubborn edit controls |
| `osActivate.ps1` | bring the app window to the foreground |
| `osWindowRect.ps1` | read window geometry (hwnd-first) |
| `osMoveWindow.ps1` | restore the recorded window position/size |
| `osDismissPopup.ps1` | Fail-and-Recover: dismiss unexpected dialogs |
| `osEscape.ps1` | Fail-and-Recover: ESC out of stuck input states |

> None of these helpers do coordinate injection: they handle keyboard input,
> window management, popup recovery, and pattern-based (Expand/Scroll/Invoke)
> element interaction — always selector-based, never screen pixels. The `.py`
> helpers use **COM IUIAutomation (comtypes)**, the same stack as
> `agent/agent.py`; earlier `.ps1` versions of `osScroll`/`osScopedInvoke`/
> `osExpandCollapse` used .NET managed UIA (`System.Windows.Automation`), which
> cannot see legacy Win32 controls (list rows, toolbar buttons, `SysTreeView32`
> tree items) and were replaced for exactly that reason. Do not edit generated
> files or the `_debug-helpers/` copies — both are overwritten on every
> Generate; fix `server/server.js` templates instead.

---

## 3. Running Generated Tests

Each generated `*TestById.js` / `*TestByClass.js` is a **standalone Node.js
script** — it does not use `describe`/`it`/`browser`/`expect`, does not read
`wdio.conf.js`, and needs no test runner:

```powershell
cd generated-wdio
npm install          # first time only — shared deps for every generated app
cd <AppName>
node <AppName>TestById.js
# e.g. cd Calculator && node CalculatorTestById.js
```

Each app folder's own `package.json` has no dependencies of its own (Node
resolves `node_modules` up the directory tree) — it exists only for
`node --run test:byid` convenience and a human-readable description; you
don't need to run `npm install` inside the app folder itself.

The script itself spawns Appium (`ensureAppium()`), creates the WinAppDriver
session, replays every recorded step, and exits with a non-zero
`process.exitCode` on failure — no separate Appium terminal, no
`@wdio/appium-service`, no WDIO config to keep in sync. The replay is
**visual**: the app launches, its window is moved back to the recorded
geometry, and each step clicks/types/scrolls the real UI in order, printed as
`[STEP] n:action label` as it happens.

> `wdio.conf.js` is no longer generated at all (2026-07-21) — it was never
> read by this execution path, so `npx wdio run` is not just unsupported,
> there's nothing left for it to run.

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

### Checkbox clicks are value-verified, not just error-checked

A plain WinAppDriver `element/click()` reports success the moment the click
call returns without throwing — it never checks whether the checkbox's
`ToggleState` actually flipped. That is a real false-PASS risk (measured on
TeamViewer's WebView2 toggles): `Legacy.Select()`/`Invoke()` fallbacks can
"succeed" while leaving the box untouched. Every `CheckBox` click (same-window
or cross-window) routes through `osScopedInvoke` instead, which reads
`ToggleState` before and after the click and only reports success if it
actually changed — falling back to a direct `TogglePattern.Toggle()` call if
the visual click alone didn't register.

### WebView2 / Electron apps

A WebView2/Electron host window attaching a WinAppDriver session successfully
does **not** mean its content is reachable — the embedded Chromium renderer
is invisible to WinAppDriver's managed UIA client no matter what. Confirmed
end-to-end on TeamViewer 15 (WebView2), three separate fixes were needed to
make this class of app replay correctly, all keyed off `element.isWebContent`
(set by `agent/agent.py` when the element's owning window hosts an embedded
Chromium child):

1. **Clicks** on `isWebContent` elements route through `osScopedInvoke` (COM
   UIA) instead of the normal WinAppDriver `element/click` — the same
   mechanism `agent.py`'s own capture uses to see these elements at all.
2. **Selectors** for `isWebContent` elements drop `className` entirely. A web
   framework's `className` is the raw DOM `class` attribute — dozens of
   Tailwind-style utility tokens including hover/active/disabled state
   classes — and an exact-match AND condition against that string breaks the
   moment a single token differs between capture and replay. Only `name` is
   used (present and stable for almost every interactive web element in
   practice).
3. **Typing** into `isWebContent` fields skips WinAppDriver's
   `element/value` (`ValuePattern.SetValue`) entirely and always falls back
   to real OS-level key injection (`osType`, `SendInput`-based). `SetValue`
   can report success with no error while a React-style app never sees a
   real keyboard event and the field stays empty — measured directly on
   TeamViewer's session-code field.
4. **Elevation must match.** Chromium enables its accessibility tree lazily
   and Windows' UIPI (User Interface Privilege Isolation) blocks a
   *lower*-privilege automation client from seeing *any* content inside a
   *higher*-privilege window — not a timing issue, no amount of waiting or
   warm-up clicks gets around it (both were tried and measured; see
   `poc/diag_teamviewer_a11y_wakeup.py` / `poc/diag_teamviewer_real_click_wakeup.py`).
   TeamViewer runs elevated, so **the agent, the Express bridge's spawned
   Appium/WinAppDriver processes, and the generated test script must all run
   from an Administrator terminal** when the target app is elevated — an
   ordinary terminal sees only the window shell (1-2 elements) no matter how
   long it waits or how many clicks it sends.

This has only been validated against one Chromium host (TeamViewer). Other
Electron/WebView2 apps are expected to behave the same way but have not been
tested — try `poc/probe_app_automatability.py --exe ...` against a new one
before assuming it "just works".

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

- **Electron/Chromium apps are supported (validated: TeamViewer/WebView2),
  not out of scope** — see **WebView2 / Electron apps** above for the three
  fixes this needed and the elevation requirement. This reverses an earlier
  (2026-07-31) verdict that called this app class categorically unautomatable
  based on a single stale measurement; re-measuring is what overturned it —
  see `CLAUDE.md` §4 for the full history if you're tempted to re-declare an
  app class "impossible" from one bad reading.
- **Single-instance apps break `launchApp()`.** It only recognizes a window
  that is *not* present in the pre-launch baseline, so starting a recording
  against an app that's already running (TeamViewer, Win11 Notepad) times out
  waiting for a "new" window that will never appear. Fully close the app
  first.
- **HeidiSQL's "더 보기" (More) overflow menu items are captured but not yet
  replayed.** The trigger is a VCL `SplitButton` whose `ExpandCollapsePattern`
  is never queryable at replay time (COM `GetCurrentPattern` always raises) —
  replay's exception handler for that case assumes "no ExpandCollapsePattern
  = this was actually a plain command button, not a menu", and invokes the
  trigger a second time instead of consulting the captured item index. The
  capture side works (position-based, same pattern as owner-drawn combos,
  ~90% success rate after a retry-budget fix) — only the replay-side
  `osExpandCollapse.py` needs a fix to check for a captured `itemIndex`
  before falling back to "just re-click the trigger".
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
- **Delphi/VCL apps** (confirmed with HeidiSQL) expose controls without a
  real declared `AutomationId` — the default Win32 UIA provider fills that
  property in with the control's own window handle instead, which is
  reassigned every launch. This id is automatically rejected in favor of a
  stable `ClassName`/`Name` selector, so most buttons/tabs/input fields work
  fine.
- **Custom-drawn (owner-drawn) controls** — HeidiSQL's session-list
  `TVirtualStringTree` is the confirmed case — expose *zero* items to UI
  Automation even though the control itself is visible and populated
  (verified directly against the live UIA tree: `FindAll`/tree-walk all
  agree on the same node count with no rows present). No selector, anchor,
  or COM-based search can reach an individual row — **recording a click on
  such a list is not currently possible**. Record a flow that avoids the
  list instead (e.g. HeidiSQL's "New" button creates and auto-selects a
  session, so the tabs beneath it become directly clickable without ever
  touching the list).
- **Dropdowns: the hard part is naming them, not opening them.** Measured
  2026-07-31 with `poc/diag_dropdowns.py` — this corrects an earlier claim
  that HeidiSQL's combos expose no items:
  - *PuTTY* — combo has a unique `AutomationId`; expanding it exposes its 5
    items (in the app window and in a separate `ComboLBox` top-level window).
    **Fully automatable.**
  - *HeidiSQL* — expanding **does** expose the items (5 and 18 respectively).
    What fails is identifying *which* combo to open. On the new-session panel
    the two combos offer no stable, unique handle: one carries an
    `AutomationId` that is really its window handle (`920954`, different every
    launch), the other has **no AutomationId and no Name at all**; both
    dropdown arrows share `AutomationId="DropDown"`, whose `Name` flips
    between `열기`/`닫기` with the open state. The network-type combo is a
    `TComboBoxEx` surfaced as a `Pane` whose `Name` **is the currently
    selected value** (`MariaDB or MySQL (TCP/IP)`, `MySQL on RDS`, …), so a
    Name-based selector only matches while that value is already chosen.
  - Mitigation in place: when several candidates share `AutomationId="DropDown"`
    in one window, resolution falls back to the recorded **relative Y position
    within the window** to pick among the structurally identical matches. This
    selects *which* matched element to act on — it never clicks a coordinate,
    so §3 still holds.
  - *Owner-drawn dropdown items* — HeidiSQL's network-type combo is a Win32
    `ComboBoxEx`: two controls share one rect, and only the inner `ComboBox`
    (empty Name **and** empty AutomationId) can be expanded. Its 18 items are
    all individually invokable but **all nameless**, so they can only be
    addressed by position. Handled since 2026-07-31: capture records
    `comboItemIndex`/`comboItemCount`, replay expands the combo and invokes the
    Nth item, and refuses to pick anything if the live list length differs from
    the recorded one. Verified end to end (value changed
    `MariaDB or MySQL (TCP/IP)` → `MySQL on RDS`). Before this, the click was
    discarded at capture time and degraded into a click on the panel behind the
    open list.

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
