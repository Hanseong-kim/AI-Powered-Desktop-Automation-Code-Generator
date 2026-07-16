# Evidence: PowerShell/Python helper necessity + inlining feasibility

Supporting detail for `RESPONSE_DRAFT.md`. Everything below is either a
direct code citation from `server/server.js` (this repo, as of
2026-07-16) or a measured result already recorded in `poc/FINDINGS.md`
/ `poc/SUBMISSION.md` / `CLAUDE.md`. Nothing here is new speculation.

---

## 1. Full inventory of generated helper files

Every generated app folder (`generated-wdio/<AppName>/`) gets these 9
files alongside its two `.js` test files. Sizes measured from the
template constants in `server/server.js`:

| File | Type | Lines | Runtime args? | Purpose |
|---|---|---:|---|---|
| `osType.ps1` | PowerShell | 13 | yes (`-b64`) | SendKeys text injection (WinAppDriver's own text-entry endpoint rejects some native edit controls, e.g. Notepad's RichEdit) |
| `osWindowRect.ps1` | PowerShell | 65 | yes (`-titleLike`/`-hwnd`/flags) | Query a window's position/size/owner without WinAppDriver (no such REST endpoint) |
| `osMoveWindow.ps1` | PowerShell | 80 | yes (`-hwnd`, geometry) | Restore a window to its recorded position/size before replay |
| `osActivate.ps1` | PowerShell | 44 | yes (`-titleLike`/`-hwnd`) | Bring a specific window to the foreground |
| `osDismissPopup.ps1` | PowerShell | 135 | yes (`-exclude`, etc.) | Fail-and-recover: scan for and dismiss an unexpected popup after a failed step |
| `osEscape.ps1` | PowerShell | 3 | no | Send the ESC key |
| `osScroll.py` | Python (COM UIA) | 165 | yes (`--hwnd --sel-b64 --delta`) | Scroll a container via `ScrollPattern`, fallback to `PostMessageW(WM_MOUSEWHEEL)` |
| `osExpandCollapse.py` | Python (COM UIA) | 187 | yes (`--hwnd --sel-b64` + item) | Expand/collapse a ComboBox/menu/tree node via `ExpandCollapsePattern` |
| `osScopedInvoke.py` | Python (COM UIA) | 160 | yes (`--hwnd --sel-b64` + trigger) | Find and click an element inside a specific window by HWND, bypassing WinAppDriver session scope entirely |

**Total: ~852 lines across 9 files.** None of this is boilerplate —
each script maps to one specific WinAppDriver limitation (see §2).

Source line ranges in `server/server.js` (constant definitions):
`OS_SCROLL_PY` 209-374, `OS_WINRECT_PS1` 384-449, `OS_MOVEWINDOW_PS1`
470-550, `OS_TYPE_PS1` 554-567, `OS_ESCAPE_PS1` 574-577,
`OS_ACTIVATE_PS1` 591-635, `OS_DISMISS_POPUP_PS1` 654-789,
`OS_EXPANDCOLLAPSE_PY` 806-993, `OS_SCOPEDINVOKE_PY` 1019-1179.

---

## 2. The 3 concrete WinAppDriver gaps, with evidence

### Gap A — no scroll endpoint

WinAppDriver's REST surface (session/element endpoints) has nothing for
scrolling a container. The only REST-only alternative would be
simulating physical mouse-wheel input at screen coordinates — which
this project already forbids everywhere else (2026-07-10 stakeholder
directive: no coordinate-based replay, ever, because it breaks on
window move/resize/resolution change).

**Measured proof** (`poc/FINDINGS.md`, PoC②, `uiaScrollExplorer.ps1`):
calling `ScrollPattern.Scroll()` directly on a File Explorer list moved
`VerticalScrollPercent` from `0` to `0.374`, with zero calls to
`SetCursorPos` or any pixel API. `osScroll.py` is this exact technique,
productionized, with a `PostMessageW(WM_MOUSEWHEEL)` fallback for
legacy controls that don't implement `ScrollPattern` at all (also
measured — `SendMessageW`, the synchronous variant, crashed
`charmap.exe` during testing, so the fallback specifically uses the
async `PostMessageW`).

### Gap B — a session sees exactly one window, for its whole life

**Measured proof** (`poc/FINDINGS.md`, PoC③, `poc3_hwndSegment.ps1`):
with 10+ unrelated top-level windows open on the desktop, a scoped
WinAppDriver session's `GET /session/{id}/window_handles` returned only
`["0x000A0C98"]` — the single handle the session was created against.
There is no `switchToWindow`-style operation across arbitrary top-level
windows the way there is in browser automation. This was independently
re-confirmed in this project's own event log
(`CLAUDE.md`, PoC③ section: *"a scoped WinAppDriver session's
window_handles returns only the single hwnd it was created with"*).

Consequence: when a click needs to land in a second window (a popup, a
"Save As" dialog, a dropdown that renders as its own top-level window —
all real, encountered cases: 7-Zip's "압축 대상 추가" dialog, PuTTY's
Configuration dropdown, FileZilla popups), the options are (a) spin up
a brand-new WinAppDriver session scoped to that window — each one costs
up to 15-20s to create — or (b) reach that window directly via Windows
UI Automation without going through a WinAppDriver session at all.
`osScopedInvoke.py` implements (b): it takes an HWND, finds the target
element inside that window's UIA subtree, and invokes it — no REST
session, no per-window session-creation cost, and immune to the
same-literal-title collision bug that title-keyed session caching
otherwise has (documented in `CLAUDE.md`, 2026-07-15 "버그2" — 7-Zip's
main window and its dialog are both literally titled "7-Zip").

### Gap C — WinAppDriver's accessibility bridge is blind to some legacy native controls

**Measured proof, live bug** (`CLAUDE.md`, 2026-07-14): on PuTTY, a
"Window" tree item needed `ExpandCollapsePattern.Expand()`, but the
first implementation (`.NET System.Windows.Automation`, which is what
WinAppDriver's own bridge is built on) returned
`ExpandCollapsePattern not supported on target` for a control that
demonstrably does support it. Switching the exact same call to the raw
Windows UI Automation **COM** interface (`IUIAutomation` via
`comtypes`, no WinAppDriver/managed-UIA layer involved) fixed it
immediately — same control, same target, only the automation stack
changed. `poc/FINDINGS.md` independently confirms the same class of gap
against `services.msc`'s legacy `SysListView32`/`SysTreeView32`
controls, and a second, unrelated real bug against 7-Zip's own list
rows (`CLAUDE.md`, 2026-07-15 "버그4": WinAppDriver's `element/click`
silently no-ops on a 7-Zip list row where a direct COM UIA `Invoke()`
on the identical element works instantly — measured before/after state,
not assumption).

This is why `osScroll.py`, `osExpandCollapse.py`, and
`osScopedInvoke.py` are all `.py` calling `comtypes`/COM UIA directly,
rather than `.ps1` calling `.NET`'s managed UIA — the managed layer is
the same one WinAppDriver itself sits on, and inherits the same blind
spots.

---

## 3. Inlining feasibility — what we checked, and why it doesn't generalize

There's already **one** precedent for zero-sibling-file inlining in
this codebase: `osForegroundHwnd()` (`server/server.js:1798-1810`),
which shells out via:

```js
execSync(`powershell -NoProfile -EncodedCommand ${OS_FOREGROUND_ENCODED}`, ...)
```

`OS_FOREGROUND_ENCODED` is a **base64-encoded, fixed** 6-line
read-only script (just `GetForegroundWindow()`), pre-encoded once as a
JS constant at codegen time (`server/server.js:1539-1551`). This works
specifically *because* it needs **zero runtime parameters** — the exact
same encoded blob is valid for every call, every generated file.

The other 8 helpers all need per-call parameters — a window handle, an
element selector, a scroll direction, and so on. PowerShell's
`-EncodedCommand` flag takes one fixed, pre-encoded script blob; it has
no equivalent of `-File script.ps1 -arg1 value1` for inline scripts.
Concretely, to inline (say) `osScopedInvoke.py`'s 160-line body with
its `--hwnd`/`--sel-b64`/`--trigger-sel-b64` arguments baked in, we'd
have to either:

- **re-encode the full ~160-line script from scratch at every single
  call site**, with the specific hwnd/selector values spliced into the
  script text before encoding — meaning the *codegen-time* JS would
  need to duplicate that base64-generation logic per call, and every
  generated test file would carry that ~160 lines (as base64, so
  larger) **once per call site** instead of once per file, or
- pass arguments as trailing positional args after
  `-EncodedCommand <blob>` (PowerShell does technically support this),
  which still requires the full script to already be encoded and
  present somewhere as source before it can be encoded — i.e. we'd
  still be maintaining a `.ps1`/`.py`-shaped source file, just not
  saving it to disk, which doesn't remove any of the actual complexity,
  only the ability to open and read it when something fails.

Either way this is strictly worse than the current approach on the
axes that matter for a generated-test deliverable:
- **Size**: current design shares one ~160-line file across every call
  in a test run; per-call-site inlining would duplicate that (as
  larger base64 text) at every call.
- **Debuggability**: a failed `osScopedInvoke.py` call today points at
  a named file with a line number; an inlined failure would point into
  an opaque base64 string inside a `child_process.execSync` call.
- **No precedent for inlined Python at all** in this codebase — `python
  -c "..."` doesn't cleanly support `argparse`-style flags the way the
  current `--hwnd --sel-b64` scripts do, and stuffing a 150-190 line
  Python script as a `-c` string reintroduces the nested-quoting
  problems this project has already hit and worked around elsewhere
  (multiple quote levels: shell → JS template string → PowerShell/
  Python string — documented failure mode in this codebase's commit
  history around 2026-07-15).

**Conclusion**: inlining is the right call for exactly the one
zero-argument, fixed script we already inlined. For the remaining 8,
which all need runtime parameters, keeping them as small, named,
single-purpose sibling files is the better tradeoff — not an oversight.
