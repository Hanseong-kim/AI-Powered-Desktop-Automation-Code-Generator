# CLAUDE.md — AI-Powered Desktop Automation Code Generator

Records interactions with a Windows desktop app (UIA/COM + pynput) and generates
standalone **WebdriverIO JavaScript** tests that replay them.

> **This file holds only durable facts — commands, architecture, rules, traps.**
> It is deliberately short. The previous version grew to 957 lines because a
> "Current Status (update every session)" section accumulated session logs; that
> is why it was deleted. **Do not add status, progress, or changelog sections
> here.** Session progress goes in `note/project/code-generator/daily/YYYY-MM-DD.md`.

---

## ⚠️ Parent CLAUDE.md does not apply to this project

`C:\hansung\project\CLAUDE.md` is loaded automatically and describes a
**different** project (Internet Download Manager, WebdriverIO v9 + TypeScript +
Mocha, `npm run wdio`, Appium on port 4724, Page Object Model under
`test/pageobjects/`). **None of that exists here.** This project emits plain
standalone JS, has no wdio test runner, no `wdio.conf.js`, no POM, and its
generated scripts self-start Appium on 4723.

It also instructs you to use `code-review-graph` MCP tools before Grep/Read —
**those tools are not available in this session** (verified 2026-07-31). Use
Grep/Glob/Read directly.

When the two files conflict, **this file wins.**

---

## 1. Commands

```powershell
.\start-dev.ps1          # launches all three processes in their own windows
```

Or individually:

```powershell
cd server; node server.js        # Express bridge  → http://localhost:3002
cd ui;     npm run dev           # React UI (Vite) → http://localhost:3000
cd agent;  python agent.py       # ADMINISTRATOR PowerShell required
                                 # must print "Administrator rights: YES"
```

Run a generated test — standalone, self-starts Appium, no runner/config needed:

```powershell
cd generated-wdio\Calculator
node CalculatorTestById.js       # folder name = PascalCase app name
```

Regression gate (server must be running, agent not needed):

```powershell
python agent\mock_events.py      # 323/323 checks as of 2026-07-31
```

> `mock_events.py` POSTs synthetic events to the live server. If you called
> `/api/events/restore` first, the mock events **overwrite that backup file** in
> `recorded-events/` (the server keeps writing to `sessionBackupFile`). Restart
> the server before running the gate, or you will destroy a real recording.

---

## 2. Architecture

```
React UI (3000) --HTTP--> Express (3002) --HTTP--> Python Agent (4444)
      ^                        |
      +------- SSE feed -------+
```

`/api/generate` is **template-based — no LLM, no API key, no network.**
(An early Groq-based generator was fully replaced; do not resurrect it.)

| File | Role |
|---|---|
| `server/server.js` | `/api/generate`, replay-architecture decision, all code templates |
| `agent/agent.py` | pynput hooks → raw queue → worker thread (UIA/COM) |
| `ui/src/components/ControlPanel.jsx` | app presets |
| `generated-wdio/<App>/` | generated output (`<App>TestById.js`, `<App>TestByClass.js`) |
| `generated-wdio/_debug-helpers/<App>/` | human-readable copies of the `os*.ps1/py` helpers |
| `poc/` | standalone diagnostics (COM UIA probes) |

**Generated `.js` is self-contained**: it embeds every `os*.ps1`/`os*.py` helper
as a string and self-extracts to a temp dir at run time (`_helperFile()`). The
`_debug-helpers/` copies exist only for humans to read — the script never loads
them.

**Two replay modes**, chosen by `needsSessionSwitching()`:
- *simple* — one window, one WinAppDriver session (`initAppHwnd`)
- *session* — multi-window; per-window scoped sessions (`getWindowSession`,
  `_switchWindow`, `_clickScoped`), plus a COM escape hatch (`osScopedInvoke.py`)
  for windows WinAppDriver cannot attach to

**Agent two-thread rule**: pynput callbacks enqueue raw dicts and return
immediately. All UIA/COM work happens on the worker thread after `CoInitialize`.

---

## 3. Hard Rules

- **Never hand-edit `generated-wdio/*`.** It is overwritten on every Generate.
  Fix `server/server.js` (templates) or `agent/agent.py` and regenerate.
- **pynput callbacks**: enqueue only. No UIA/COM inside hooks.
- **No stored coordinates.** Selectors are AutomationId / ClassName / Name XPath.
  Recorded x/y stay in the capture JSON for dedupe and diagnostics only and must
  never reach generated code.
  - *Permitted (2026-07-24 redefinition)*: a **dynamic ClickablePoint + SendInput**
    computed at replay time from a freshly resolved element — this is what
    WinAppDriver's own `element/click` does. Allowed **only** in the COM escape
    path (`COM_INPUT_PY`'s `send_input_click()`), must pass the offscreen /
    `WindowFromPoint` PID / `ElementFromPoint` round-trip checks, must fall back
    to `Invoke()`/`Toggle()`/`Select()` when they fail, and must log
    `[COM-SendInput]`.
  - An event with no usable selector generates an **explicit FAIL step**, never a
    silent coordinate fallback.
- **No false PASS.** A replay action counts as success only when the app's state
  provably changed. `Legacy.Select()` returning without error is not proof — it
  leaves a checkbox untouched. Verify (e.g. compare `ToggleState` before/after).
- **No Java, no Playwright, no Python output.** WebdriverIO JS only.
- **Regression gate before claiming done**: `python agent/mock_events.py` must
  stay green, and anything not GUI-verified must be labelled UNVERIFIED — never
  written up as working.
- **Event scope**: Click, Type, DoubleClick, Scroll. Drag is out of scope.
- **Scroll** via UIA `ScrollPattern`, falling back to hwnd-scoped
  `PostMessageW(WM_MOUSEWHEEL)`. Never `SendMessage` (crashed charmap in PoC),
  never physical wheel injection at screen coordinates.
- **`agent.py` has no hot reload** — restart it after editing.

---

## 4. What this tool can and cannot automate

**Before recording against a new app, probe it:**

```powershell
python poc\probe_app_automatability.py --exe "C:\Path\To\App.exe"
python poc\probe_app_automatability.py --hwnd 134050 --json report.json
```

It prints a tier verdict with the raw measurements behind it. Validated against
PuTTY (Tier 1), HeidiSQL (Tier 3, correctly names the owner-drawn session list),
TeamViewer (Tier 4). Run this *first* — it turns "it doesn't work" into a
measurement, and it is the intended answer to "is app X in scope?".


"Native app" is not a useful category. The real question is **whether the app
implements a UIA provider that is both reachable and wired to real behaviour.**
Four tiers, established by measurement:

| Tier | Example | Status |
|---|---|---|
| **1. OS-standard controls** — Win32/WinForms/WPF/MFC | PuTTY, 7-Zip, FileZilla, Notepad, Calculator | **Works.** One provider, one tree, UIA actions reach real handlers. |
| **2. Custom framework, accessibility implemented** | Delphi/VCL (HeidiSQL), wxWidgets (FileZilla tabs) | **Works with per-family fixes** — e.g. VCL fills AutomationId with the window handle (rejected automatically); wx tabs need `LegacyIAccessible`. |
| **3. Owner-drawn, no accessibility objects** | HeidiSQL session list (`TVirtualStringTree`), Qt/QML `MouseArea` | **Not possible.** The control paints pixels and publishes *zero* UIA children. Nothing to select. |
| **4. Web content in a native frame** | Electron, CEF, **WebView2** — VSCode, **TeamViewer 15** | **Not possible in this architecture.** See below. |

### Tier 4 detail (measured 2026-07-31, TeamViewer 15.79)

TeamViewer looks native but its whole UI is WebView2:
`MainWindowOne → TV_WebView2Control → Chrome_WidgetWin_0/1 → RootWebArea`.

- `FindAll(Subtree)` from the **app window** returns **2 elements** — no web
  content at all. Reproduced three times independently.
- `FindAll(Subtree)` from **`Chrome_WidgetWin_0`** returns **53** — the whole UI.
- `ElementFromPoint` (hit-test) always returns real web elements.

That asymmetry is why **capture works and replay does not**: `agent.py` captures
by hit-test; replay searches downward from the app window.

Rooting the search at the Chromium child hwnd *finds* the element — but nothing
**actuates** it: `GetClickablePoint` refuses, `SendInput` at the element's own
rect (same PID, window foreground, verified) leaves `ToggleState` at 0, and
`TogglePattern.Toggle()` returns without error and changes nothing.

Diagnostics: `poc/diag_teamviewer_tree.py`, `poc/diag_teamviewer_a11y_wakeup.py`,
`poc/diag_teamviewer_id_stability.py`, `poc/diag_teamviewer_human_click.py`.

Dead ends already tried — **do not retry**:
- accessibility "warm-up" (hit-test probe, or `ElementFromHandle` on the renderer
  child): the app-window subtree never grows
- `WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=--force-renderer-accessibility`: needs
  control of the launch environment and still leaves actuation broken
- inspecting the element to harvest AutomationId/ClassName: the controls in
  question expose **empty** AutomationId *and* ClassName; the ids that do exist
  (`TextField113`, `field-542`, `button-466`) are React/Fluent render counters
  that change between builds

The only real path for Tier 4 is a hybrid architecture (CDP/WebDriver for web
content, UIA for the native frame). That is a separate project, not a fix.

---

## 5. Known Traps

- **Agent not elevated** → `automationId`/`name` come back empty. Check the
  startup banner every time.
- **Main-window events carry no `rootHwndHex`.** Only clicks in a separate
  top-level window get one. Any guard written as
  `next.rootHwndHex && e.rootHwndHex && ...` is silently dead for the
  main-window case — this deleted trigger clicks from generated tests
  (fixed 2026-07-31; the 7-Zip `추가`→`확인` flow was affected too).
- **Single-instance apps break `launchApp()`.** It only accepts a window that is
  *not* in the pre-launch baseline, so relaunching an already-running app
  (TeamViewer, Win11 Notepad) times out. Known open issue.
- **UWP window titles change during load** — match with `contains(@Name,...)`.
- **Numeric AutomationIds are not unique in classic Win32 dialogs** (PuTTY reuses
  resource ids across panels). Resolution ANDs id + name + className first.
- **Dropdowns fail on identification, not on opening** (measured 2026-07-31,
  `poc/diag_dropdowns.py`). Expanding a combo does expose its items — PuTTY 5,
  HeidiSQL 5 and 18 — in the app window *and* in a separate `ComboLBox`
  top-level window. What breaks is naming the combo: HeidiSQL's combos carry
  either an hwnd-as-AutomationId (changes every launch) or nothing at all, both
  arrows share `AutomationId="DropDown"` with a `Name` that flips 열기/닫기, and
  the `TComboBoxEx` network-type combo surfaces as a `Pane` whose `Name` *is the
  currently selected value*. Disambiguation falls back to the recorded relative
  Y within the window — choosing among matched elements, never clicking a point.
- **A Win32 `ComboBoxEx` is TWO controls stacked on one rect** (HeidiSQL
  네트워크 유형, measured 2026-07-31 — `poc/diag_heidisql_comboex.py`):
  the outer `TComboBoxEx` (UIA **Pane**, `Name` = the selected value,
  `AutomationId` = its own hwnd, patterns = **Legacy only**, cannot be
  expanded) and an inner `ComboBox` (`Name`/`AutomationId` both empty,
  patterns = ExpandCollapse/Value/Legacy). **Only the inner one is drivable.**
  Its open list publishes 18 items, each supporting Invoke/SelectionItem, but
  **every item `Name` is empty** (owner-drawn, icon-per-item), so items are
  addressable only by position.
  - Capture: `agent.py`'s "click point outside the adopted element's rect"
    guard used to discard these clicks entirely — an open dropdown always
    hit-tests to the combo, whose rect is still the *collapsed* box. It now
    calls `Inspector.open_dropdown_item_at()` first and records
    `comboItemIndex`/`comboItemCount` instead of dropping the selector.
  - Replay: codegen forwards both to `osExpandCollapse(..., itemIndex,
    itemCount)`, which expands and picks the Nth item, refusing to pick at all
    if the live list length no longer matches the recorded one. A structural
    index, not a coordinate — same principle as `SLOT_INDEX_CONTROL_TYPES`.
  - Verified end to end 2026-07-31: value changed
    `MariaDB or MySQL (TCP/IP)` → `MySQL on RDS` (item #4 of 18).
  - Regression gate: `MockComboIndex` scenario in `mock_events.py`.
- **A `TComboBoxEx`'s own Name is the currently-selected value** — a click
  that lands on the collapsed combo's body (not the arrow, not an open list
  item) hit-tests to the `TComboBoxEx` Pane itself and captures that Name.
  A selector built from it can only match *after* that value is already
  selected — never at replay start (`click-not-found://Pane[@ClassName=
  "TComboBoxEx" and @Name="..."]`, measured live 2026-07-31). Fixed the same
  way as the `DropDown` arrow's state-dependent 열기/닫기 Name:
  `isStateDependentValueDisplay()` detects `className === 'TComboBoxEx'` and
  `comSafeTarget(el, { forceDropName: true })` strips Name unconditionally
  (`dropNameIfStableId` alone doesn't cover it — the automationId is the
  element's own hwnd, so `isWindowHandleId` already zeroes `stableId`, and
  `dropNameIfStableId` only fires when `stableId` is truthy). Routes through
  the same `osScopedInvoke(..., null, relY)` COM path as the DropDown arrow.
  Verified end to end 2026-07-31 against the live app. Regression gate:
  `MockComboBoxExReclick` scenario in `mock_events.py`.
- **A Korean titlebar Close button is `Button[@Name="닫기"]`** — the same name a
  Win32 ComboBox dropdown arrow can carry. Dropdown arrows must always resolve by
  AutomationId (`~DropDown`), never by a bare name, or replay closes the app.
- **.NET managed UIA cannot see legacy Win32 controls** (list rows, toolbar
  buttons, `SysTreeView32` items). Every replay helper uses COM `IUIAutomation`
  via comtypes — the same stack `agent.py` uses. Do not introduce
  `System.Windows.Automation`.
- **comtypes `FindFirst` returns a NULL COM pointer, not `None`, on a miss.**
  `if el is not None` is always true; test truthiness (`if el:`).
- **PowerShell `-File` reads a BOM-less script as CP949**, mangling Korean button
  names. Every emitted `.ps1` needs a UTF-8 BOM (both `saveFiles()` and the
  runtime temp extraction).
- **WordPad** was removed from Win11 24H2 — do not add it back as a preset.

---

## 6. Scope (stakeholder requirements)

- Generic: point it at any `.exe` or UWP AUMID — no per-app integration.
- Desktop only. Not a browser automation tool.
- JavaScript output only, complete and runnable.
- XPath-only targeting; coordinates forbidden (see §3 for the precise boundary).
- Multi-window handled by HWND tracking: capture each new window's HWND, group
  subsequent events under that segment, emit a window switch before the segment.
- **Replay must be visibly real-time** — the script must perform the recorded
  actions on screen, not just launch the app.
- **Popups handled dynamically** — never require a pristine pre-recording state.
  Handled by the Fail-and-Recover wrapper (`_step()` + `osDismissPopup.ps1`).

Grading weights: capture 25% · element inspection 20% · generated code quality
25% · architecture/live feed 15% · reliability 10% · docs/demo 5%
