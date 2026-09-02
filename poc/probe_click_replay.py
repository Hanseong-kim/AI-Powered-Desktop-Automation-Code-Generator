"""
================================================================================
  Click Replay Dry-Run
  Point at one control, before recording it, and find out whether THIS
  specific click will replay correctly -- without going through
  record -> generate -> replay first.
================================================================================

WHAT THIS IS FOR

    probe_app_automatability.py answers a STATIC question: "does this screen
    publish usable UIA controls?" That misses most of what actually breaks a
    replay in this project, because the real failures are almost all TIMING /
    STATE bugs that only show up when you actually interact with the control:

      - the control destroys itself on click (a Cancel/Close/Login button) --
        the worker thread's re-read finds nothing where the click landed
      - a checkbox reports "clicked successfully" via SelectionItem/Legacy
        without its ToggleState ever changing (false PASS)
      - the click opens a new top-level window (menu/dialog) -- must be
        captured as a cross-window event, not a same-window one
      - the element has no name/AutomationId/ClassName at all -- capture has
        nothing to build a selector from and the step will FAIL

    This script performs ONE real click through the exact same COM mechanism
    the generated tests use (SendInput, not a programmatic Invoke -- see
    server.js send_input_click()), and diffs the element's state before and
    after. It tells you which of the above you are about to hit, before you
    spend time recording a whole flow around it.


HOW TO RUN IT

    Point your mouse at the control you want to test (do not click it), then:

        python probe_click_replay.py --title "7-Zip"

    The script waits up to 15s for you to press ENTER while hovering. It
    reads the control under the cursor at that instant -- BEFORE doing
    anything -- so your own click never contaminates the "before" snapshot.

    This loops: after each capture it goes right back to waiting, so you can
    hover a second control and press ENTER again, and a third, and so on --
    handy for walking a whole address-bar breadcrumb (7-Zip's "컴퓨터" >
    "C:" > "hansung" segments) in one run without relaunching the script or
    re-matching --title. Press ESC (instead of ENTER) when done; a summary
    table of every point captured that run prints at the end, with anything
    that got a non-OK verdict called out separately.

    Add --live to actually perform the click and compare before/after state.
    Without --live it only reports what it sees (same as probe_app_
    automatability.py's per-control section, but for exactly the one control
    under your cursor). --live performs a REAL click -- if the control closes
    a dialog, submits a form, or launches something, that really happens.

    Add --double for a double-click instead of a single click.


IS IT SAFE?

    Without --live: read-only, same guarantee as probe_app_automatability.py.
    With --live: performs one real click/double-click on the control you
    pointed at. Nothing else is different from what a generated test's
    replay step would do.


WHAT THE VERDICT MEANS

    OK                  addressable, actionable, and (with --live) survived
                         the click with the state change you'd expect.
    NO-SELECTOR         no name/AutomationId/ClassName -- capture will emit
                         an explicit FAIL step for this click (by design,
                         see CLAUDE.md SS3 "no false PASS").
    SELF-DESTROYING     the element could not be re-read after the click
                         (rect invalid / provider gone). Replay needs the
                         dead-element-recovery path (agent.py _inspect()).
    FALSE-SUCCESS-RISK  a TogglePattern exists but its state did not change
                         after the click -- standard WAD click reporting
                         would claim success while nothing happened.
    CROSS-WINDOW        a new top-level window appeared after the click --
                         this event must be captured/replayed as cross-window
                         (osScopedInvoke / isCrossWindowEvent), not same-window.
    OWNER-DRAWN-SUSPECT the element sits inside a List/Tree/Grid/ComboBox
                         container that publishes zero items downward -- the
                         rows on screen may not be individually selectable.

    Several can apply to the same click at once (e.g. a menu item that is
    also cross-window). All that apply are printed.
"""

import argparse
import ctypes
import os
import sys
import time
from ctypes import wintypes

if sys.platform != "win32":
    print("Windows only.")
    sys.exit(1)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from probe_app_automatability import (
        get_uia, describe, patterns_of, activate, all_windows, pids_for_image,
        CONTROL_TYPE_NAMES, CONTAINERS,
    )
except ImportError as e:
    print("This script must sit next to probe_app_automatability.py "
          "(imports its window-finding and UIA helpers).\n%s" % e)
    sys.exit(1)

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

u = ctypes.windll.user32
TreeScope_Subtree = 7
TreeScope_Ancestors = 32  # not a real UIA constant name but unused; ancestors via RawViewWalker below

UIA_TogglePatternId = 10015
VK_RETURN = 0x0D
VK_ESCAPE = 0x1B

MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_VIRTUALDESK = 0x4000
SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN = 76, 77
SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 78, 79


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", ctypes.c_long), ("dy", ctypes.c_long),
                ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_void_p)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("mi", MOUSEINPUT)]


INPUT_MOUSE = 0


def wait_for_enter_at_cursor(timeout, prompt=None):
    """Poll for a physical Enter/Escape keypress and report the cursor
    position at the instant Enter went down -- never the click itself, so
    the 'before' snapshot below is never contaminated by the interaction
    being tested.

    Returns ("enter", (x, y)), ("esc", None), or ("timeout", None).
    """
    print(prompt or (
        "Hover the mouse over the control you want to test, then press "
        "ENTER (do not click it) -- waiting up to %ds... (ESC to finish)"
        % timeout))
    deadline = time.time() + timeout
    was_enter_down = bool(u.GetAsyncKeyState(VK_RETURN) & 0x8000)
    was_esc_down = bool(u.GetAsyncKeyState(VK_ESCAPE) & 0x8000)
    while time.time() < deadline:
        enter_down = bool(u.GetAsyncKeyState(VK_RETURN) & 0x8000)
        esc_down = bool(u.GetAsyncKeyState(VK_ESCAPE) & 0x8000)
        if enter_down and not was_enter_down:
            pt = wintypes.POINT()
            u.GetCursorPos(ctypes.byref(pt))
            return "enter", (pt.x, pt.y)
        if esc_down and not was_esc_down:
            return "esc", None
        was_enter_down = enter_down
        was_esc_down = esc_down
        time.sleep(0.02)
    return "timeout", None


def snapshot(uia, x, y):
    """Same read agent.py's _inspect() does: ElementFromPoint, then describe
    what's there. Returns None if nothing resolves (matches the 'no
    resolvable element' drop path)."""
    try:
        el = uia.ElementFromPoint(wintypes.POINT(int(x), int(y)))
    except Exception as e:
        return None, "ElementFromPoint failed: %s" % e
    if not el:
        return None, "no element at point"
    d = describe(el)
    try:
        r = el.CurrentBoundingRectangle
        d["rect"] = (r.left, r.top, r.right, r.bottom)
    except Exception as e:
        d["rect"] = None
        d["rectError"] = str(e)
    try:
        d["hwnd"] = el.CurrentNativeWindowHandle
    except Exception:
        d["hwnd"] = 0
    try:
        d["pid"] = el.CurrentProcessId
    except Exception:
        d["pid"] = 0
    d["patterns"] = patterns_of(el)
    if "Toggle" in d["patterns"]:
        try:
            tp = el.GetCurrentPattern(UIA_TogglePatternId)
            d["toggleState"] = tp.CurrentToggleState
        except Exception:
            d["toggleState"] = None
    return (el, d), None


def top_level_windows_snapshot():
    return {w["hwnd"]: w["title"] for w in all_windows()}


def send_click(x, y, double=False):
    """Mirrors server.js send_input_click()'s mouse mechanics exactly --
    same SendInput flags, same move/down/up spacing, same double-click
    timing -- so what this measures is what replay actually does, not a
    different click mechanism that happens to also work."""
    vx = u.GetSystemMetrics(SM_XVIRTUALSCREEN)
    vy = u.GetSystemMetrics(SM_YVIRTUALSCREEN)
    vw = u.GetSystemMetrics(SM_CXVIRTUALSCREEN)
    vh = u.GetSystemMetrics(SM_CYVIRTUALSCREEN)
    nx = int(round((x - vx) * 65535.0 / (vw - 1)))
    ny = int(round((y - vy) * 65535.0 / (vh - 1)))

    def send(flags):
        inp = INPUT(type=INPUT_MOUSE)
        inp.mi = MOUSEINPUT(nx, ny, 0, flags, 0, 0)
        return u.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))

    send(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK)
    time.sleep(0.04)
    send(MOUSEEVENTF_LEFTDOWN | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK)
    time.sleep(0.04)
    send(MOUSEEVENTF_LEFTUP | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK)
    if double:
        time.sleep(0.05)
        send(MOUSEEVENTF_LEFTDOWN | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK)
        time.sleep(0.04)
        send(MOUSEEVENTF_LEFTUP | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK)
    time.sleep(0.3)  # give the app time to react before we re-read anything


def is_owner_drawn_suspect(uia, el, d):
    """Same signal as probe_app_automatability.py section 3, narrowed to just
    this element's ancestor chain: does it sit inside a List/Tree/Grid/
    ComboBox that publishes zero item-type descendants?"""
    try:
        walker = uia.RawViewWalker
    except Exception:
        return None
    item_cond = uia.CreateOrCondition(
        uia.CreatePropertyCondition(30003, 50007),
        uia.CreateOrCondition(
            uia.CreatePropertyCondition(30003, 50024),
            uia.CreateOrCondition(
                uia.CreatePropertyCondition(30003, 50029),
                uia.CreatePropertyCondition(30003, 50019))))
    cur = el
    for _ in range(6):
        try:
            cur = walker.GetParentElement(cur)
        except Exception:
            return None
        if not cur:
            return None
        cd = describe(cur)
        if cd["ctId"] in CONTAINERS:
            try:
                n = cur.FindAll(TreeScope_Subtree, item_cond).Length
            except Exception:
                n = -1
            return (CONTAINERS[cd["ctId"]], n)
    return None


def capture_one(uia, x, y, args):
    """Runs the full snapshot (+ optional --live click) sequence for one
    point, printing the same detailed output the single-shot mode always
    printed. Returns a summary dict for the end-of-run table:
    {point, controlType, name, verdicts} where verdicts is a list of
    verdict-name strings (empty means OK)."""
    win_titles_before = top_level_windows_snapshot() if args.live else None

    result, err = snapshot(uia, x, y)
    if result is None:
        print("=" * 78)
        print("VERDICT: NO-SELECTOR")
        print("=" * 78)
        print("  reason: %s" % err)
        print("  nothing resolvable at this point -- capture would drop the "
              "selector and codegen would emit an explicit FAIL step.")
        return {"point": (x, y), "controlType": None, "name": None,
                "verdicts": ["NO-SELECTOR"]}
    el, before = result

    print("--- element under cursor -------------------------------------------------")
    print("  controlType : %s" % before["controlType"])
    print("  name        : %r" % before["name"])
    print("  automationId: %r" % before["automationId"])
    print("  className   : %r" % before["className"])
    print("  rect        : %s" % (before["rect"],))
    print("  patterns    : %s" % (", ".join(before["patterns"]) or "NONE"))
    if "toggleState" in before:
        print("  toggleState : %s" % before["toggleState"])
    print()

    verdicts = []
    addressable = bool(before["automationId"] or before["name"] or before["className"])
    actionable = bool(before["patterns"])
    if not addressable:
        verdicts.append(("NO-SELECTOR",
                          "no name/AutomationId/ClassName -- capture would drop this selector"))
    if not actionable:
        verdicts.append(("NO-ACTIONABLE-PATTERN",
                          "no Invoke/Toggle/SelectionItem/ExpandCollapse/Value/Legacy pattern -- "
                          "nothing for the replay chain to call"))

    od = is_owner_drawn_suspect(uia, el, before)
    if od and od[1] == 0:
        verdicts.append(("OWNER-DRAWN-SUSPECT",
                          "sits inside a %s that publishes 0 items downward -- if it visibly "
                          "has rows, they are not individually selectable" % od[0]))

    if not args.live:
        print("(ran without --live: this is description only, no click was performed)")
        print()
        print("=" * 78)
        print("VERDICT%s" % (": " + ", ".join(v for v, _ in verdicts) if verdicts else ": OK (static)"))
        print("=" * 78)
        for v, why in verdicts:
            print("  - %s: %s" % (v, why))
        if not verdicts:
            print("  addressable and has an actionable pattern. Live behavior (dead-element,")
            print("  false-success, cross-window) still unverified -- re-run with --live.")
        return {"point": (x, y), "controlType": before["controlType"], "name": before["name"],
                "verdicts": [v for v, _ in verdicts]}

    print("--- performing %s click via SendInput (same mechanism replay uses) ---"
          % ("double-" if args.double else ""))
    send_click(x, y, double=args.double)

    win_titles_after = top_level_windows_snapshot()
    new_windows = {h: t for h, t in win_titles_after.items() if h not in win_titles_before}
    closed_windows = {h: t for h, t in win_titles_before.items() if h not in win_titles_after}

    result2, err2 = snapshot(uia, x, y)
    print("--- element at the same point, after the click ----------------------------")
    if result2 is None:
        print("  %s" % err2)
        after = None
    else:
        _, after = result2
        print("  controlType : %s" % after["controlType"])
        print("  name        : %r" % after["name"])
        print("  rect        : %s" % (after["rect"],))
        if "toggleState" in after:
            print("  toggleState : %s" % after["toggleState"])
    print()

    dead = (after is None) or (after.get("rect") in (None, (0, 0, 0, 0)))
    if dead:
        verdicts.append(("SELF-DESTROYING",
                          "the element could not be re-read after the click (rect invalid / "
                          "provider gone) -- replay needs the dead-element-recovery path "
                          "(agent.py _inspect(), 'FIRST read caught it alive')"))

    if "toggleState" in before:
        after_toggle = after.get("toggleState") if after else None
        if after_toggle == before["toggleState"]:
            verdicts.append(("FALSE-SUCCESS-RISK",
                              "TogglePattern present but ToggleState did not change "
                              "(%s -> %s) -- a plain click-reports-success check would "
                              "silently PASS with nothing actually toggled"
                              % (before["toggleState"], after_toggle)))

    if new_windows:
        verdicts.append(("CROSS-WINDOW",
                          "new top-level window(s) appeared: %s -- must be captured/replayed "
                          "as a cross-window event" % list(new_windows.values())))
    if closed_windows:
        verdicts.append(("WINDOW-CLOSED",
                          "top-level window(s) closed: %s" % list(closed_windows.values())))

    print("=" * 78)
    print("VERDICT%s" % (": " + ", ".join(v for v, _ in verdicts) if verdicts else ": OK"))
    print("=" * 78)
    for v, why in verdicts:
        print("  - %s: %s" % (v, why))
    if not verdicts:
        print("  addressable, actionable, survived the click, no window/state surprises.")
        print("  This click should record and replay cleanly.")
    return {"point": (x, y), "controlType": before["controlType"], "name": before["name"],
            "verdicts": [v for v, _ in verdicts]}


def print_summary(captures):
    if not captures:
        return
    print("=" * 78)
    print("SUMMARY (%d capture%s)" % (len(captures), "" if len(captures) == 1 else "s"))
    print("=" * 78)
    for i, c in enumerate(captures, 1):
        v = ", ".join(c["verdicts"]) if c["verdicts"] else "OK"
        print("  #%-2d (%5d,%5d)  %-12s %-20r  %s"
              % (i, c["point"][0], c["point"][1], c["controlType"] or "-",
                 c["name"], v))
    flagged = [(i, c) for i, c in enumerate(captures, 1) if c["verdicts"]]
    if flagged:
        print()
        print("flagged:")
        for i, c in flagged:
            print("  #%-2d %s -- %s" % (i, c["name"], ", ".join(c["verdicts"])))


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--hwnd", type=int)
    g.add_argument("--pid", type=int)
    g.add_argument("--title", help="substring of the window title")
    g.add_argument("--exe", help="path or image name of an already-running app")
    ap.add_argument("--live", action="store_true",
                     help="perform a REAL click and compare before/after state "
                          "(default: describe only, no interaction)")
    ap.add_argument("--double", action="store_true", help="double-click instead of click")
    ap.add_argument("--wait", type=int, default=15,
                     help="seconds to wait for you to press ENTER over the target")
    args = ap.parse_args()

    if args.hwnd:
        wins = [w for w in all_windows() if w["hwnd"] == args.hwnd]
    elif args.pid:
        wins = [w for w in all_windows() if w["pid"] == args.pid and w["title"]]
    elif args.title:
        q = args.title.lower()
        wins = [w for w in all_windows() if q in w["title"].lower()]
    else:
        image = args.exe.split("\\")[-1]
        pids = pids_for_image(image)
        wins = [w for w in all_windows() if w["pid"] in pids and w["title"]]

    if not wins:
        print("no matching window found.")
        return 1
    target = max(wins, key=lambda w: (w["rect"][2] - w["rect"][0]) * (w["rect"][3] - w["rect"][1]))
    activate(target["hwnd"])
    print("target window: hwnd=%d title=%r\n" % (target["hwnd"], target["title"]))

    uia, mod = get_uia()
    captures = []
    prompt = None  # first iteration uses wait_for_enter_at_cursor's default message
    while True:
        status, point = wait_for_enter_at_cursor(args.wait, prompt=prompt)
        prompt = ("Hover the next control and press ENTER -- waiting up to "
                  "%ds... (ESC to finish and see the summary)" % args.wait)
        if status == "esc":
            print("(ESC) finishing.\n")
            break
        if status == "timeout":
            print("timed out waiting for ENTER -- finishing with what was captured so far.\n")
            break
        x, y = point
        print("captured point: (%d,%d)\n" % (x, y))
        captures.append(capture_one(uia, x, y, args))
        print()

    print_summary(captures)
    return 0


if __name__ == "__main__":
    sys.exit(main())
