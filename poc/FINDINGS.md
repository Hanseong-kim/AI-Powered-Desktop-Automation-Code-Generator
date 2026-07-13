# PoC Results — Coordinate-Free UIA Automation Technical Validation (executed 2026-07-10, PoC ③ E2E completed 07-12)

All 3 PoCs were run directly on this machine to validate feasibility. Scripts are
preserved in this folder. All were run **without admin rights, calling
WinAppDriver/Appium REST directly** — standalone PowerShell/UIA COM scripts,
reproducible independent of `agent.py`/the capture pipeline.

## PoC ① — Clean XPath capture + coordinate-free click (`dumpUia.ps1`)

```
powershell -File dumpUia.ps1 -ProcessName mmc -MaxDepth 6
```

**Result: possible, already partially proven.**
- Ran against `services.msc` (opened non-elevated) → live-confirmed that real
  native Win32 controls expose numeric AutomationIds: `SysListView32`
  id=`12786`, `SysTreeView32` id=`12785`, and many toolbar buttons. An XPath
  like `//*[@AutomationId="12786"]` is directly usable.
- This repo's `generated-wdio/Calculator/CalculatorTestById.js` has already
  **passed GUI verification** via this exact path (`el.click()` on an
  AutomationId-XPath) (session 13) — not new work, just reconfirming an
  already-verified mechanism.
- **Important caveat (found via regedit)**: `regedit.exe` runs elevated per
  its manifest. A non-admin script can read the top-level window's
  ClassName/AutomationId (`SysTreeView32` id=1 confirmed), but **child nodes
  (TreeItems) are completely invisible, blocked by UIPI (integrity-level
  blocking)** — `Stop-Process` also failed with "Access is denied",
  reconfirming the process runs elevated. This matches CLAUDE.md §5's
  "Agent not Admin" trap exactly — **not a tool defect but a permission
  requirement of the target app**. Consistent with the stakeholder already
  having narrowed scope to native-only, non-admin apps (FileZilla/PuTTY/7-Zip).

## PoC ② — Pixel-free scroll (`uiaScroll.ps1`, `uiaScrollExplorer.ps1`, `verifyScroll2.ps1`)

```
powershell -File uiaScrollExplorer.ps1 -TitleSubstring System32
```

**Result: possible, verified with live measurements.**
- In an Explorer window (`UIItemsView`, modern DirectUI ListView), calling
  `ScrollPattern.Scroll()` → measured `VerticalScrollPercent` change from
  0 → 0.374. **Zero calls to pixel APIs such as `SetCursorPos`.**
- **Caveat**: legacy controls (`services.msc`'s `SysListView32`, `charmap`'s
  `CharGridWClass`) do **not** expose UIA `ScrollPattern` — common for
  MSAA-era controls. For this case, implemented and tested an hwnd-scoped
  `WM_MOUSEWHEEL` (`SendMessageW`) fallback — delivered only to the specific
  hwnd, no coordinates/`SetCursorPos`, so it satisfies the requirement (no
  external PowerShell injecting physical signals at screen coordinates).
  **However, during this fallback test, `charmap.exe` terminated for an
  unknown reason** — suggesting `SendMessageW` (synchronous send) can
  conflict with some legacy window procedures. **Recommendation: switch the
  production fallback to `PostMessageW` (asynchronous)** — a separate test
  sending `WM_CLOSE` via `PostMessage` worked without issue.
- **Conclusion**: try ScrollPattern first, and only fall back to the
  hwnd-scoped PostMessage wheel when unsupported — this two-tier strategy is
  the proven path that satisfies the "scroll without pixel coordinates"
  requirement.

## PoC ③ — HWND segmentation + multi-window (`poc3_hwndSegment.ps1`)

**Result: conditionally possible — the existing architecture (a new scoped
session per window) is the right answer; the "switchToWindow within a single
session" alternative is not supported by WinAppDriver (confirmed empirically).**
- Via WinAppDriver REST, created a scoped session on `mspaint.exe`'s
  (non-admin, UWP package) hwnd → ran `GET /session/{id}/window_handles`.
- **Measured result**: even with 10+ other top-level windows open on the
  desktop (VSCode, Chrome, Notepad, regedit, etc.), `window_handles` returned
  **only the single hwnd the session was created on** (`["0x000A0C98"]`). In
  other words, WinAppDriver's `window_handles` is not desktop-global like a
  browser's "tab list" — it's **pinned to the window the session was created
  against** — a new window (dialog) opening does not get picked up by that
  session's `window_handles`.
- This **confirms that the architecture the current project already uses
  (create a new scoped session per new hwnd, or fall back to the Root session
  for owned windows — CLAUDE.md 07-09 item) was not a workaround but the
  necessary design given WinAppDriver's actual constraints**. The
  optimization hypothesis of "switchToWindow faster within one session" is
  rejected.
- **HWND segmentation on the capture side itself** (detect new hwnd → split
  event groups) is already implemented and GUI-verified in
  `agent.py`/`server.js` (VSCode, 07-08) — this PoC additionally clarified
  the limitation on the replay side's mechanism.
- The part that actually opens a dialog to trigger the hwnd switch
  end-to-end wasn't completed this session, because Paint's WinUI3 menu
  doesn't open via `InvokePattern` (needs separate handling such as
  `ExpandCollapsePattern`) — **→ completed 2026-07-12, see section below.**

## PoC ③ E2E Completion (added 2026-07-12, `poc3_dialog_e2e.py`)

```
python poc/poc3_dialog_e2e.py     # no admin required, only comtypes needed
```

**Result: success — the full path passed, from opening a secondary window
→ capturing its unique HWND → isolated clicking within that window's
context.** Measured output:

```
[1] explorer hwnd=0x1209fc class=CabinetWClass title='poc - 파일 탐색기'
[2] found ListItem 'FINDINGS' — SetFocus + SelectionItemPattern.Select() (no coords)
[3] opening Properties via Alt+Enter (keyboard — no coords)
[4] NEW dialog hwnd=0x12d0b0a class=#32770 title='FINDINGS 속성'
[5a] '취소' scoped to the EXPLORER window subtree: not found — isolation holds
[5b] '취소' resolved INSIDE the captured dialog subtree — invoking via UIA
     InvokePattern (element click, no coords)
[5b] dialog closed after scoped click: YES
```

- Target: Explorer (always non-elevated, modern UIA — the same app used for
  the ScrollPattern measurement in PoC ②). Selected the file item via UIA
  **`SelectionItemPattern`** (element-based), opened the Properties dialog
  with Alt+Enter, captured the **new `#32770` hwnd via an EnumWindows diff**,
  then found/invoked (element click) the 'Cancel' ('취소') button **scoped
  only to the captured hwnd's UIA subtree**. The same query scoped to the
  main window's subtree found nothing — isolation confirmed. Zero calls to
  `SetCursorPos`/`mouse_event`/pixel coordinates.
- Stack: **COM IUIAutomation (comtypes) — same as production `agent.py`**.
  First tried .NET `System.Windows.Automation` (managed UIA), but confirmed
  empirically that its MSAA proxy is too weak to expose the internals of
  legacy controls (list rows, toolbar buttons) at all — the managed-version
  script was discarded; COM is the right choice (reconfirms the validity of
  the product's stack choice).
- **Reasons the original target `services.msc` (MMC) was excluded (2
  empirical findings)**: ① on this machine it runs **elevated** per a
  `highestAvailable` manifest → a non-elevated script's UIA child queries
  and key injection are all blocked by UIPI (the same as the regedit trap in
  PoC ① — `Stop-Process` also denied). ② aside from elevation, the virtual
  (LVS_OWNERDATA) `SysListView32` doesn't expose UIA row items at all (true
  for both .NET and COM). → legacy MMC-style apps are unsuitable automation
  targets; consistent with the stakeholder-recommended app list
  (FileZilla/PuTTY/7-Zip — all non-elevated + standard controls).

## Side-effect / safety notes (not related to stakeholder requirements, kept for internal record)

- `regedit.exe` was left running elevated (PID varies per session) — cannot
  be terminated from a non-admin session (Access denied). The user needs to
  close it manually.
- Found that Notepad (Win11, single-instance) was holding the user's
  **actual unsaved tabs** (other project files, etc.) — this PoC only
  performed read-only UIA queries and sent no input to that process
  (confirmed safe). Recommend **avoiding Notepad** as a future automation
  target, since tab merging makes it hard to distinguish "my tab" from "the
  user's tab."
