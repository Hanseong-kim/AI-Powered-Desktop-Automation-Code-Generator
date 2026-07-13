# PoC Submission — Technical Validation of 3 Coordinate-Free UIA Desktop Automation Capabilities

Submitted: 2026-07-12 · Detailed measurement log: [`FINDINGS.md`](FINDINGS.md)

All 3 required items are reproducible via the standalone scripts in this
repository, and all run without admin rights. Zero uses of
`SetCursorPos`/`mouse_event`/pixel coordinates across the entire process.

---

## ① Launch .exe → click element → capture clean AutomationId/ClassName-based XPath

**Conclusion: possible — doubly proven via script measurement + product pipeline
passing the GUI.**

| Evidence | Details |
|---|---|
| `dumpUia.ps1` | `powershell -File poc/dumpUia.ps1 -ProcessName <name>` — dumps the UIA tree of a running app to confirm AutomationId/ClassName exposure. Live-confirmed numeric AutomationIds on native Win32 controls in services.msc (`SysListView32` id=12786, etc.) |
| Product E2E (2026-07-12) | The full record → generate → replay pipeline passed on the actual GUI: **Calculator 2 passed, Notepad 2 passed** (`npx wdio run`). Example clean XPaths captured — `//Button[@ClassName="Button" and @Name="새 탭 추가"]`, `//MenuItem[@ClassName="Microsoft.UI.Xaml.Controls.MenuBarItem" and @Name="보기"]`, `~num7Button` (AutomationId). Used as-is in replay with no manual correction |

**Caveat**: apps with an admin manifest (e.g. regedit) have their child nodes
blocked by UIPI when run from a non-elevated process — this is a property of
the target app, not a tool defect, and is resolved by the current operating
practice of running the capture agent as admin.

---

## ② Scroll a native element via UIA/InputSimulator without pixel math

**Conclusion: possible — verified with live measurements via the two-tier
strategy of ScrollPattern first, hwnd-scoped PostMessage wheel fallback second.**

| Evidence | Details |
|---|---|
| `uiaScrollExplorer.ps1` | `powershell -File poc/uiaScrollExplorer.ps1 -TitleSubstring <window title>` — calls `ScrollPattern.Scroll()` on the Explorer list, measured `VerticalScrollPercent` change of **0 → 0.374**. Zero pixel APIs |
| Product implementation (osScroll.ps1) | The above strategy is already implemented as the production replay helper — re-resolves the scroll container captured at record time via UIA, calls ScrollPattern, and only falls back to `PostMessageW(WM_MOUSEWHEEL)` for unsupported legacy controls. Passed a live measurement on Explorer (0→0.17) |

**Caveat (found via measurement)**: legacy controls (MMC ListView,
CharGrid) do not expose ScrollPattern → fallback required. The fallback must
be **PostMessage (asynchronous)** — the synchronous `SendMessageW` crashed
charmap.exe during testing.

---

## ③ Open a secondary window/popup → capture its unique HWND → isolate clicks to that window's context only

**Conclusion: possible — full E2E measured and passed (2026-07-12).**

| Evidence | Details |
|---|---|
| `poc3_dialog_e2e.py` | `python poc/poc3_dialog_e2e.py` — ① selects a file item in Explorer via UIA `SelectionItemPattern` (element-based), ② opens the Properties dialog, ③ **captures the new top-level `#32770` HWND via an EnumWindows diff**, ④ a query for the 'Cancel' button is **not found scoped to the main window's subtree, only found scoped to the captured HWND's subtree** → clicked via UIA Invoke → dialog close confirmed |
| `poc3_hwndSegment.ps1` | Measured that a WinAppDriver session's `window_handles` is pinned to the single hwnd the session was created on — proving that the "new scoped session per new window" approach (already adopted by the product) is not a workaround but a necessary design given WAD's constraints |
| Product implementation | HWND segmentation on the capture side (detect new hwnd → isolate events) and per-window scoped sessions on the replay side are already implemented and GUI-verified in `agent.py`/`server.js` (multi-window scenario, 2026-07-08) |

Measured output (excerpt):

```
[2] found ListItem 'FINDINGS' — SetFocus + SelectionItemPattern.Select() (no coords)
[4] NEW dialog hwnd=0x12d0b0a class=#32770 title='FINDINGS 속성'
[5a] '취소' scoped to the EXPLORER window subtree: not found — isolation holds
[5b] '취소' resolved INSIDE the captured dialog subtree — invoking via UIA
     InvokePattern (element click, no coords)
[5b] dialog closed after scoped click: YES
```

**Caveat (found via measurement, reflected in target-app selection)**:
legacy MMC-style apps (services.msc) are unsuitable for automation because
① they run elevated (UIPI-blocked) and ② their virtual list views don't
expose UIA items — confirming that the recommended test-app list
(FileZilla/PuTTY/7-Zip: non-elevated + standard controls) is technically the
right choice as well.

---

## Summary

| Requirement | Verdict | Key evidence |
|---|---|---|
| ① Clean XPath capture | **Possible** | Passed on product GUI (Calculator/Notepad) + dumpUia.ps1 |
| ② Pixel-free scroll | **Possible** | ScrollPattern measured 0→0.374 + production osScroll.ps1 |
| ③ HWND isolation | **Possible** | poc3_dialog_e2e.py E2E passed + WAD constraint proven |

All three techniques have been proven with independent scripts, and all of
①②③ are already reflected in the product pipeline itself, which has passed
actual GUI tests (Calculator/Notepad 2 passed).
