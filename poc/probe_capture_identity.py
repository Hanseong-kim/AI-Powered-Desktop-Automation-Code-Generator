r"""
================================================================================
  Capture Identity Probe
  Does agent.py's element_at() return the control the user actually aimed at?
================================================================================

WHAT THIS IS FOR

    This is the check `mock_events.py` structurally cannot do. That gate POSTs
    synthetic events to the server and only ever exercises server.js codegen —
    CLAUDE.md says so explicitly, and it is why every agent.py capture change
    so far could only be validated by a full manual re-recording.

    This calls the real `UIAInspector.element_at()` — the exact function the
    capture worker thread uses — against a live window, at the exact centre of
    a control identified by AutomationId, and asserts the element that comes
    back IS that control. No pynput, no recording session, no elevation beyond
    whatever the target app itself needs.

    Found with it, 2026-09-01 (Medflow HTA, MedflowMain.hta):

        aimed at : Button   AutomationId='settingsBtn'  rect=(1311,782,1405,808)
        got back : Text     AutomationId=''  Name='Glaucoma'  rect=(1356,793,1402,804)

    element_at() hit-tests correctly (ElementFromPoint returns settingsBtn),
    then "deepens": because the button is 94px wide it trips the
    `w > 80 or h > 80` container heuristic, and smallest_element_at() is run
    against the WHOLE WINDOW rather than the hit element's own subtree. The
    right-hand panel's table is clipped by `overflow:hidden` but still reports
    unclipped UIA rects that reach down over the footer, so a 506px^2 'Glaucoma'
    cell that is not a descendant of the button — not related to it at all —
    wins on area and replaces the correct hit.

    Note the clipped elements report IsOffscreen=False, so an IsOffscreen
    filter does NOT fix this (measured; see poc/diag_offscreen_hittest.py).

HOW TO RUN IT

    Inspect the app at the integrity level it runs at (CLAUDE.md §4): an app
    launched by the elevated agent.py needs an elevated shell here too. An app
    you launched yourself does not.

        python poc\probe_capture_identity.py --title Medflow --aid settingsBtn

    Add --launch to start MedflowMain.hta first and close it afterwards:

        python poc\probe_capture_identity.py --launch --aid settingsBtn

    Exit code 0 = element_at() returned the aimed-at control.
    Exit code 1 = it returned something else (the bug above).

    Clicks nothing. Only reads UIA properties.
"""

import argparse
import ctypes
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                os.pardir, "agent"))

import agent as agentmod  # noqa: E402  (path must be set first)

HTA = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir,
                   "mock-app", "medflow-hta", "MedflowMain.hta")


def top_level(uia):
    root = uia.GetRootElement()
    walker = uia.ControlViewWalker
    child = walker.GetFirstChildElement(root)
    out = []
    while child:
        try:
            out.append((str(child.CurrentName), int(child.CurrentNativeWindowHandle),
                        child))
        except Exception:
            pass
        child = walker.GetNextSiblingElement(child)
    return out


def force_foreground(u32, hwnd):
    """SetForegroundWindow, past the foreground lock.

    A plain SetForegroundWindow from a background process is refused by
    Windows (measured 2026-09-01: the call returns and the foreground window
    is unchanged). Attaching this thread's input queue to the current
    foreground thread's lifts that restriction for the duration.
    """
    SW_RESTORE = 9
    u32.ShowWindow(hwnd, SW_RESTORE)
    cur = u32.GetForegroundWindow()
    tid_cur = u32.GetWindowThreadProcessId(cur, None)
    tid_me = ctypes.windll.kernel32.GetCurrentThreadId()
    attached = False
    if tid_cur and tid_cur != tid_me:
        attached = bool(u32.AttachThreadInput(tid_me, tid_cur, True))
    try:
        u32.BringWindowToTop(hwnd)
        u32.SetForegroundWindow(hwnd)
    finally:
        if attached:
            u32.AttachThreadInput(tid_me, tid_cur, False)
    time.sleep(0.6)


def sweep(ins, win):
    """Aim at the centre of every AutomationId'd control and see what comes back.

    The point is not that 100% must resolve — some controls genuinely hit-test
    to a child or a container, and CLAUDE.md records several such families as
    correct behaviour. The point is the BEFORE/AFTER comparison: run this on
    both sides of a change to element_at() and diff the two lists.
    """
    # The target's pixels must actually be the target's. Measured 2026-09-01:
    # a first run of this sweep silently reported controls resolving to
    # 'NotificationCenterGrid'/'알림' and a '라이브러리' Hyperlink — the Windows
    # notification centre and another app had come up over the target, and
    # element_at() was faithfully reporting what was under those screen
    # coordinates. Numbers taken without this guard are not measurements of
    # the code under test.
    #
    # Foreground is only best-effort (an ELEVATED window on screen cannot be
    # pushed behind by this unelevated process at all), so the real guard is
    # the per-point WindowFromPoint ownership check further down — that one
    # holds regardless of z-order.
    u32 = ctypes.windll.user32
    force_foreground(u32, win[1])
    fg = u32.GetForegroundWindow()
    fg_root = u32.GetAncestor(fg, 2) or fg   # GA_ROOT
    if fg_root != win[1]:
        print(f"[warn ] target window is not foreground (fg={fg_root}, "
              f"want={win[1]}); each point is checked for ownership instead")

    try:
        wr = win[2].CurrentBoundingRectangle
        win_rect = (wr.left, wr.top, wr.right, wr.bottom)
    except Exception:
        win_rect = None
    print(f"[window] rect={win_rect}")

    arr = win[2].FindAll(7, ins._uia.CreateTrueCondition())
    targets, outside = [], 0
    for i in range(arr.Length):
        el = arr.GetElement(i)
        try:
            aid = el.CurrentAutomationId
            if not aid:
                continue
            r = el.CurrentBoundingRectangle
            rect = (r.left, r.top, r.right, r.bottom)
            if rect[2] <= rect[0] or rect[3] <= rect[1]:
                continue
            # Skip controls whose rect leaves the window. Those are the
            # clipped-overflow elements this investigation is about: UIA
            # reports them unclipped, but no user can aim at a point that is
            # not inside the window, so "what does element_at() return there"
            # is not a question about this code — it is a question about
            # whichever other window owns those pixels.
            if win_rect and not (win_rect[0] <= rect[0] and win_rect[1] <= rect[1]
                                 and rect[2] <= win_rect[2]
                                 and rect[3] <= win_rect[3]):
                outside += 1
                continue
            targets.append((aid, rect, el.CurrentControlType))
        except Exception:
            continue
    print(f"[sweep] skipped {outside} controls whose rect leaves the window")

    class PT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    # HWNDs are 64-bit here; ctypes' default c_int restype silently truncates
    # them, which would make every comparison below fail for a high handle.
    u32.WindowFromPoint.argtypes = [PT]
    u32.WindowFromPoint.restype = ctypes.c_void_p
    u32.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    u32.GetAncestor.restype = ctypes.c_void_p

    def owned_by_target(cx, cy):
        """Do these screen pixels actually belong to the window under test?

        Cheaper and stricter than demanding foreground: a point covered by
        some other window is not a measurement of this code at all, it is a
        measurement of that window. The same round-trip server.js's COM click
        path already uses (CLAUDE.md §3, WindowFromPoint check).
        """
        h = u32.WindowFromPoint(PT(cx, cy))
        if not h:
            return False
        return (u32.GetAncestor(h, 2) or h) == win[1]

    ok, bad, covered = 0, [], 0
    for aid, rect, ct in targets:
        cx, cy = (rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2
        if not owned_by_target(cx, cy):
            covered += 1
            continue
        try:
            got_el = ins.element_at(cx, cy)
            got = ins.describe(got_el) if got_el is not None else {}
        except Exception as e:
            got = {"automationId": f"ERR:{type(e).__name__}"}
        if got.get("automationId") == aid:
            ok += 1
        else:
            bad.append((aid, ct, rect,
                        got.get("automationId"), got.get("name"),
                        got.get("controlType")))

    print(f"[sweep] {len(targets)} controls with an AutomationId")
    if covered:
        print(f"[sweep] {covered} skipped — another window covers their centre")
    print(f"[sweep] {ok} resolve to themselves, {len(bad)} do not")
    print()
    for aid, ct, rect, gid, gname, gct in sorted(bad):
        print(f"  MISS {aid:<22} ct={ct:<6} rect={str(rect):<28} "
              f"-> aid={str(gid)!r} name={str(gname)[:24]!r} ct={gct!r}")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--title", default="", help="window title substring")
    ap.add_argument("--hwnd", type=int, default=0)
    ap.add_argument("--aid", default="",
                    help="AutomationId of the control to aim at")
    ap.add_argument("--sweep", action="store_true",
                    help="test EVERY control in the window that has an "
                         "AutomationId, and report how many element_at() "
                         "resolves to themselves — the blast-radius measure")
    ap.add_argument("--launch", action="store_true",
                    help="launch MedflowMain.hta first, close it at the end")
    args = ap.parse_args()

    # Same DPI mode the capture agent runs in — element_at() takes SCREEN
    # coordinates, and a different awareness level silently shifts them.
    agentmod._enable_per_monitor_dpi_awareness()

    proc = None
    if args.launch:
        proc = subprocess.Popen(["mshta.exe", os.path.abspath(HTA)])
        time.sleep(4)
        if not args.title:
            args.title = "Medflow"

    try:
        ins = agentmod.UIAInspector()
        tops = top_level(ins._uia)
        win = None
        if args.hwnd:
            win = next((t for t in tops if t[1] == args.hwnd), None)
        if win is None and args.title:
            win = next((t for t in tops
                        if args.title.lower() in t[0].lower()), None)
        if win is None:
            print(f"!! no top-level window matching "
                  f"title={args.title!r} hwnd={args.hwnd}")
            print(f"   {len(tops)} windows visible; if that number is tiny you "
                  "are probably not elevated (CLAUDE.md §4)")
            return 2
        print(f"[window] {win[0]!r} hwnd={win[1]}")

        if args.sweep:
            return sweep(ins, win)
        if not args.aid:
            print("!! give --aid or --sweep")
            return 2
        cond = ins._uia.CreatePropertyCondition(30011, args.aid)  # AutomationId
        target = win[2].FindFirst(7, cond)                        # Subtree
        if not target:      # comtypes returns a NULL pointer, not None
            print(f"!! no descendant with AutomationId={args.aid!r}")
            return 2
        want = ins.describe(target)
        r = want.get("rect")
        if not isinstance(r, tuple):
            print(f"!! AutomationId={args.aid!r} has no readable rect: {r}")
            return 2
        cx, cy = (r[0] + r[2]) // 2, (r[1] + r[3]) // 2
        print(f"[aimed ] ct={want.get('controlType')} "
              f"aid={want.get('automationId')!r} name={want.get('name')!r} "
              f"rect={r} centre=({cx},{cy})")

        # Same ownership check the sweep does. Without it a window sitting on
        # top of the target turns this into a measurement of THAT window —
        # and if it is elevated, ElementFromPoint raises E_ACCESSDENIED
        # (measured 2026-09-01) which reads like a code failure but is not.
        u32 = ctypes.windll.user32

        class PT(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

        u32.WindowFromPoint.argtypes = [PT]
        u32.WindowFromPoint.restype = ctypes.c_void_p
        u32.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        u32.GetAncestor.restype = ctypes.c_void_p
        force_foreground(u32, win[1])
        h = u32.WindowFromPoint(PT(cx, cy))
        owner = (u32.GetAncestor(h, 2) or h) if h else None
        if owner != win[1]:
            print(f"!! ({cx},{cy}) belongs to hwnd={owner}, not to the target "
                  f"window {win[1]} — another window is covering the control. "
                  "Close or move it and retry; this is not a result.")
            return 2

        got_el = ins.element_at(cx, cy)
        got = ins.describe(got_el) if got_el is not None else {}
        trace = getattr(ins, "_last_trace", None) or {}
        print(f"[got   ] ct={got.get('controlType')} "
              f"aid={got.get('automationId')!r} name={got.get('name')!r} "
              f"rect={got.get('rect')!r}")
        print(f"[trace ] picked_by={trace.get('picked_by')!r} "
              f"root_hwnd={trace.get('root_hwnd')!r}")
        print(f"[trace ] raw={trace.get('raw')}")
        print()

        ok = (got.get("automationId") == args.aid)
        if ok:
            print(f"PASS  element_at() returned {args.aid!r}")
            return 0
        print(f"FAIL  aimed at AutomationId={args.aid!r} but element_at() "
              f"returned aid={got.get('automationId')!r} "
              f"name={got.get('name')!r} ct={got.get('controlType')!r}")
        return 1
    finally:
        if proc is not None:
            try:
                proc.terminate()
            except Exception:
                pass


if __name__ == "__main__":
    sys.exit(main())
