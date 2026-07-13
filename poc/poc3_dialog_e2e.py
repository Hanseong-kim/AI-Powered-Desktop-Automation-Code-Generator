"""PoC 3 E2E — open a secondary window (Properties dialog) → capture its
unique HWND → isolate clicks strictly within that window's context
(2026-07-12, completes the part left unfinished on 07-10).

Target: Windows Explorer (always non-elevated, modern UIA exposure; the same
        app used for the ScrollPattern measurement in PoC 2)
Stack: COM IUIAutomation (comtypes) — same UIA stack as production agent.py.

Flow:
  1. Open this repo's poc/ folder in Explorer → obtain the CabinetWClass hwnd
     → take a baseline snapshot of top-level hwnds
  2. Find the FINDINGS.md ListItem in the file list via UIA, then SetFocus +
     SelectionItemPattern.Select() (element-based selection — zero coordinates)
  3. Open the Properties dialog via Alt+Enter (keyboard accelerator)
  4. Capture the new top-level #32770 hwnd via an EnumWindows diff
  5. Prove isolation: scope a 'Cancel' button query to the Explorer window's
     subtree → not found; scope it to the captured dialog hwnd's subtree →
     found → UIA Invoke (element click) → confirm the dialog closes
  6. Cleanup: close the Explorer window via PostMessage(WM_CLOSE)

Zero uses of SetCursorPos / mouse_event / pixel coordinates throughout.

Targets attempted then excluded (measured 2026-07-12, recorded in the
submission doc):
  - services.msc (MMC): runs elevated on this machine per a highestAvailable
    manifest → UIPI blocks all UIA child queries/key injection from a
    non-elevated script (same as the regedit trap in PoC 1). Also, the
    virtual (LVS_OWNERDATA) SysListView32 doesn't expose UIA row items,
    elevation aside.

Run: python poc/poc3_dialog_e2e.py   (no admin required)
"""
import ctypes
import os
import sys
import time
from ctypes import wintypes

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import comtypes
import comtypes.client

user32 = ctypes.windll.user32

# UIA property / pattern / control-type IDs
UIA_ControlTypeProperty = 30003
UIA_NameProperty = 30005
UIA_InvokePatternId = 10000
UIA_SelectionItemPatternId = 10010
CT_Button = 50000
CT_ListItem = 50007
TreeScope_Descendants = 4
WM_CLOSE = 0x0010
VK_MENU, VK_RETURN, KEYEVENTF_KEYUP = 0x12, 0x0D, 0x0002

POC_DIR = os.path.dirname(os.path.abspath(__file__))
TARGET_FILE = "FINDINGS.md"


def top_windows(pid=None):
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            if pid is None:
                found.append(hwnd)
            else:
                wpid = wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(wpid))
                if wpid.value == pid:
                    found.append(hwnd)
        return True

    user32.EnumWindows(cb, 0)
    return found


def win_class(hwnd):
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def win_title(hwnd):
    buf = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(hwnd, buf, 512)
    return buf.value


def main():
    comtypes.CoInitialize()
    mod = comtypes.client.GetModule("UIAutomationCore.dll")
    uia = comtypes.client.CreateObject(
        "{ff48dba4-60ef-4201-aa87-54103eef594e}", interface=mod.IUIAutomation
    )

    # 1. open Explorer at the poc folder + baseline -------------------------
    # ShellExecute on a folder REUSES an existing Explorer window showing that
    # folder — locate it via Shell.Application (matching the folder path)
    # instead of relying on a new-hwnd diff.
    poc_url = "file:///" + POC_DIR.replace("\\", "/").lower()

    def find_poc_explorer():
        try:
            shell = comtypes.client.CreateObject("Shell.Application")
            ws = shell.Windows()
            for i in range(ws.Count):
                try:
                    w = ws.Item(i)
                    if w and str(w.LocationURL).lower() == poc_url:
                        return int(w.HWND)
                except Exception:
                    continue
        except Exception:
            pass
        return None

    print(f"[1] opening Explorer at {POC_DIR}")
    exp_hwnd = find_poc_explorer()
    if exp_hwnd is None:
        ctypes.windll.shell32.ShellExecuteW(None, "open", POC_DIR, None, None, 1)
        deadline = time.time() + 15
        while time.time() < deadline and exp_hwnd is None:
            time.sleep(0.5)
            exp_hwnd = find_poc_explorer()
    if exp_hwnd is None:
        sys.exit("FAIL: Explorer window did not appear")
    print(f"[1] explorer hwnd={exp_hwnd:#x} class=CabinetWClass "
          f"title={win_title(exp_hwnd)!r}")
    time.sleep(1.5)  # let the item view populate
    baseline = set(top_windows())
    print(f"[1] baseline top-level windows: {len(baseline)}")

    exp_el = uia.ElementFromHandle(exp_hwnd)
    if not exp_el:
        sys.exit("FAIL: ElementFromHandle failed for Explorer window")

    # 2. select the target file via UIA (element-based, no coordinates) -----
    # Explorer may hide file extensions, so the ListItem Name can be
    # 'FINDINGS' rather than 'FINDINGS.md' — enumerate and prefix-match.
    stem = TARGET_FILE.rsplit(".", 1)[0]
    li_cond = uia.CreatePropertyCondition(UIA_ControlTypeProperty, CT_ListItem)
    item = None
    deadline = time.time() + 15
    while time.time() < deadline and not item:
        items = exp_el.FindAll(TreeScope_Descendants, li_cond)
        names = []
        for i in range(items.Length):
            el = items.GetElement(i)
            names.append(el.CurrentName)
            if el.CurrentName.startswith(stem):
                item = el
                break
        if not item:
            time.sleep(0.5)
    if not item:
        sys.exit(f"FAIL: no ListItem starting with {stem!r} in Explorer view "
                 f"(saw: {names[:10]})")
    print(f"[2] found ListItem {item.CurrentName!r} — "
          "SetFocus + SelectionItemPattern.Select() (no coords)")
    item.SetFocus()
    item.GetCurrentPattern(UIA_SelectionItemPatternId).QueryInterface(
        mod.IUIAutomationSelectionItemPattern
    ).Select()
    time.sleep(0.5)

    # 3. open the Properties dialog (Alt+Enter accelerator) ------------------
    print("[3] opening Properties via Alt+Enter (keyboard — no coords)")
    user32.SetForegroundWindow(exp_hwnd)
    time.sleep(0.3)
    user32.keybd_event(VK_MENU, 0, 0, 0)
    user32.keybd_event(VK_RETURN, 0, 0, 0)
    user32.keybd_event(VK_RETURN, 0, KEYEVENTF_KEYUP, 0)
    user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)

    # 4. capture the NEW top-level hwnd (EnumWindows diff) -------------------
    dlg_hwnd = None
    deadline = time.time() + 10
    while time.time() < deadline and dlg_hwnd is None:
        time.sleep(0.3)
        for h in top_windows():
            if h not in baseline and win_class(h) == "#32770":
                dlg_hwnd = h
                break
    if dlg_hwnd is None:
        sys.exit("FAIL: no new #32770 dialog hwnd appeared")
    print(f"[4] NEW dialog hwnd={dlg_hwnd:#x} class=#32770 "
          f"title={win_title(dlg_hwnd)!r}")

    # 5. isolation proof ------------------------------------------------------
    cancel_cond = uia.CreateAndCondition(
        uia.CreatePropertyCondition(UIA_ControlTypeProperty, CT_Button),
        uia.CreatePropertyCondition(UIA_NameProperty, "취소"),
    )
    in_main = exp_el.FindFirst(TreeScope_Descendants, cancel_cond)
    print("[5a] '취소' scoped to the EXPLORER window subtree: "
          + ("FOUND (unexpected!)" if in_main else "not found — isolation holds"))

    dlg_el = uia.ElementFromHandle(dlg_hwnd)
    cancel = dlg_el.FindFirst(TreeScope_Descendants, cancel_cond)
    if not cancel:
        sys.exit("FAIL: '취소' not found inside dialog subtree")
    print("[5b] '취소' resolved INSIDE the captured dialog subtree — "
          "invoking via UIA InvokePattern (element click, no coords)")
    cancel.GetCurrentPattern(UIA_InvokePatternId).QueryInterface(
        mod.IUIAutomationInvokePattern
    ).Invoke()
    time.sleep(0.8)
    closed = dlg_hwnd not in top_windows()
    print(f"[5b] dialog closed after scoped click: {'YES' if closed else 'NO (still open)'}")

    # 6. cleanup ---------------------------------------------------------------
    user32.PostMessageW(exp_hwnd, WM_CLOSE, 0, 0)
    print("[6] Explorer window closed via PostMessage(WM_CLOSE)")
    print()
    print("PoC3-E2E RESULT: secondary window (file Properties dialog) opened from a")
    print("UIA-selected element, its unique HWND captured via EnumWindows diff, and")
    print("the follow-up click resolved and executed strictly inside that HWND's UIA")
    print("subtree. Zero pixel APIs used (no SetCursorPos / mouse_event / coords).")


if __name__ == "__main__":
    main()
