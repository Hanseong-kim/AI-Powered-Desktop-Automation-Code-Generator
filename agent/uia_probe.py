"""
agent/uia_probe.py — STEP 0 assumption probe (standalone; does NOT touch agent.py)

Goal: prove whether clicking Calculator (UWP) buttons delivers Windows UI
Automation events (Invoke / FocusChanged / StructureChanged) carrying the
real automationId/name (e.g. "num2Button").

  events arrive -> UIA-event redesign is viable.
  no events     -> redesign assumption is false; rethink direction.

Run from an ADMINISTRATOR PowerShell, then click calc buttons / type numbers:
    python agent/uia_probe.py [seconds]
Ctrl+C or wait for the timeout to stop.
"""

import sys
import time
import subprocess

import ctypes
import win32gui

import comtypes
import comtypes.client

UIA_CLSID = "{ff48dba4-60ef-4201-aa87-54103eef594e}"
UIA_Invoke_InvokedEventId = 20009
TreeScope_Subtree = 4
DEFAULT_SECONDS = 90
CALC_EXE = r"C:\Windows\System32\calc.exe"


def log(*a):
    print("[probe]", *a, flush=True)


def describe(elem):
    info = {"automationId": "", "name": "", "controlType": ""}
    if elem is None:
        return info
    for k, getter in (
        ("automationId", lambda: elem.CurrentAutomationId),
        ("name", lambda: elem.CurrentName),
        ("controlType", lambda: elem.CurrentControlType),
    ):
        try:
            info[k] = getter() or ""
        except Exception:
            pass
    return info


def make_handlers(mod):
    class InvokeHandler(comtypes.COMObject):
        _com_interfaces_ = [mod.IUIAutomationEventHandler]
        def HandleAutomationEvent(self, sender, eventId):
            try:
                d = describe(sender)
                log(f"INVOKE   id='{d['automationId']}' name='{d['name'][:30]}' ct={d['controlType']}")
            except Exception as e:
                log("INVOKE handler error:", e)

    class FocusHandler(comtypes.COMObject):
        _com_interfaces_ = [mod.IUIAutomationFocusChangedEventHandler]
        def HandleFocusChangedEvent(self, sender):
            try:
                d = describe(sender)
                log(f"FOCUS    id='{d['automationId']}' name='{d['name'][:30]}' ct={d['controlType']}")
            except Exception as e:
                log("FOCUS handler error:", e)

    class StructHandler(comtypes.COMObject):
        _com_interfaces_ = [mod.IUIAutomationStructureChangedEventHandler]
        def HandleStructureChangedEvent(self, sender, changeType, runtimeId):
            try:
                d = describe(sender)
                log(f"STRUCT   change={changeType} id='{d['automationId']}' name='{d['name'][:30]}'")
            except Exception as e:
                log("STRUCT handler error:", e)

    return InvokeHandler(), FocusHandler(), StructHandler()


def find_calc_windows():
    found = []
    def _enum(hwnd, _):
        try:
            if not win32gui.IsWindowVisible(hwnd):
                return True
            cls = win32gui.GetClassName(hwnd)
            title = win32gui.GetWindowText(hwnd)
            if cls == "ApplicationFrameWindow" and title.strip():
                found.append((hwnd, title))
        except Exception:
            pass
        return True
    win32gui.EnumWindows(_enum, None)
    return found


def main():
    seconds = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_SECONDS

    import pythoncom
    pythoncom.CoInitialize()  # STA on this thread

    mod = comtypes.client.GetModule("UIAutomationCore.dll")
    uia = comtypes.client.CreateObject(UIA_CLSID, interface=mod.IUIAutomation)

    is_admin = bool(ctypes.windll.shell32.IsUserAnAdmin())
    log(f"Administrator: {'YES' if is_admin else 'NO  (ids/names may be EMPTY!)'}")

    log(f"Launching {CALC_EXE} ...")
    try:
        subprocess.Popen([CALC_EXE])
    except Exception as e:
        log("launch failed:", e)

    hwnds = []
    deadline = time.time() + 8.0
    while time.time() < deadline:
        cands = find_calc_windows()
        if cands:
            hwnds = cands
            break
        time.sleep(0.3)

    if not hwnds:
        log("No ApplicationFrameWindow found — only GLOBAL focus handler will register.")
    else:
        for hwnd, title in hwnds:
            log(f"found window hwnd={hwnd} title='{title}'")

    invoke_h, focus_h, struct_h = make_handlers(mod)
    anchors = [invoke_h, focus_h, struct_h]  # GC anchors — keep alive

    try:
        uia.AddFocusChangedEventHandler(None, focus_h)
        log("registered: GLOBAL FocusChanged")
    except Exception as e:
        log("FocusChanged registration FAILED:", e)

    for hwnd, title in hwnds:
        try:
            elem = uia.ElementFromHandle(hwnd)
            uia.AddAutomationEventHandler(
                UIA_Invoke_InvokedEventId, elem, TreeScope_Subtree, None, invoke_h)
            log(f"registered: Invoke (subtree) on hwnd={hwnd}")
        except Exception as e:
            log(f"Invoke registration FAILED hwnd={hwnd}:", e)
        try:
            elem = uia.ElementFromHandle(hwnd)
            uia.AddStructureChangedEventHandler(
                elem, TreeScope_Subtree, None, struct_h)
            log(f"registered: StructureChanged (subtree) on hwnd={hwnd}")
        except Exception as e:
            log(f"StructureChanged registration FAILED hwnd={hwnd}:", e)

    log("")
    log("=== Now CLICK calculator buttons / type numbers ===")
    log(f"=== Watch for INVOKE id='num2Button' etc. Runs {seconds}s (Ctrl+C to stop) ===")
    log("")

    end = time.time() + seconds
    try:
        while time.time() < end:
            pythoncom.PumpWaitingMessages()  # delivers STA UIA events; Ctrl+C friendly
            time.sleep(0.05)
    except KeyboardInterrupt:
        log("interrupted")
    finally:
        try:
            uia.RemoveAllEventHandlers()
        except Exception:
            pass
        anchors.clear()
        log("done. handlers removed.")


if __name__ == "__main__":
    main()
