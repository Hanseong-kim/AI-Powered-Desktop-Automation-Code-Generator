r"""
================================================================================
  MSHTML <select> Dropdown Probe
  Are the <option>s missing, or do they die when the expanding process exits?
================================================================================

WHY

    Replay of the 2026-09-02 Medflow recording fails like this:

        [STEP] 13:expandCollapse
        [osExpandCollapse] state after Expand() = 1        <- the list DID open
        [STEP] 14:click TC1
        [osScopedInvoke] failed: target not found under main window or any
                                 other top-level window

    while the capture of the very same clicks succeeded, with names:

        15  click ComboBox  aid='deptSelect'   root=2E0BFC   (the main window)
        16  click ListItem  name='TC1'         root=F0060    <- a DIFFERENT
        17  click ListItem  name='Pediatrics'  root=100060      top-level window
        18  click ListItem  name='TC2'         root=110060      each time

    So "the app does not expose its options" and "replay cannot reach them"
    are two different claims. This probe separates them by reproducing
    replay's exact process structure, because that is the one thing the
    recording did NOT do: replay runs each step as its own short-lived Python
    process, so Expand() and the item click never share a process.

WHAT IT MEASURES

    --phase expand   opens the dropdown via ExpandCollapsePattern (exactly what
                     osExpandCollapse.py does), then, IN THE SAME PROCESS,
                     enumerates every top-level window, names the one that
                     appeared, and dumps its items. Writes the main window's
                     hwnd to a hand-off file and exits.

    --phase find     a FRESH process — what osScopedInvoke.py is at step 14.
                     Re-enumerates and searches for the item by name, first
                     under the main window, then under every top-level window,
                     the same two-stage search osScopedInvoke does.

    --phase both     runs expand as a real child process, lets it exit, then
                     runs the find in this one. This is the actual replay
                     sequence end to end.

HOW TO RUN

        python poc\probe_select_dropdown.py --launch --phase both
        python poc\probe_select_dropdown.py --hwnd 3214332 --phase expand

    Inspect the app at the integrity level it runs at (CLAUDE.md section 4).
"""

import argparse
import ctypes
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                os.pardir, "agent"))

import agent as agentmod  # noqa: E402  (path must be set first)

HTA = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir,
                   "mock-app", "medflow-hta", "MedflowMain.hta")
HANDOFF = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "_select_probe_hwnd.json")

UIA_ExpandCollapsePatternId = 10005
UIA_AutomationIdPropertyId = 30011
UIA_NamePropertyId = 30005
UIA_ControlTypePropertyId = 30003
TreeScope_Subtree = 7
CT_LISTITEM = 50007


def win_class(hwnd):
    buf = ctypes.create_unicode_buffer(256)
    ctypes.windll.user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def top_level(uia):
    """Every top-level window UIA can see, as (name, hwnd, class, element)."""
    root = uia.GetRootElement()
    walker = uia.ControlViewWalker
    child = walker.GetFirstChildElement(root)
    out = []
    while child:
        try:
            h = int(child.CurrentNativeWindowHandle)
            out.append((str(child.CurrentName), h, win_class(h), child))
        except Exception:
            pass
        child = walker.GetNextSiblingElement(child)
    return out


def find_window(ins, hwnd=None, title=None):
    tops = top_level(ins._uia)
    if hwnd:
        w = next((t for t in tops if t[1] == hwnd), None)
        if w:
            return w, tops
    if title:
        w = next((t for t in tops if title.lower() in t[0].lower()), None)
        if w:
            return w, tops
    return None, tops


def dump_items(ins, element, limit=40):
    """Every ListItem in a subtree, in tree order."""
    cond = ins._uia.CreatePropertyCondition(UIA_ControlTypePropertyId, CT_LISTITEM)
    arr = element.FindAll(TreeScope_Subtree, cond)
    n = arr.Length
    rows = []
    for i in range(min(n, limit)):
        el = arr.GetElement(i)
        try:
            rows.append((str(el.CurrentName), str(el.CurrentAutomationId)))
        except Exception:
            rows.append(("<unreadable>", ""))
    return n, rows


def phase_expand(args):
    ins = agentmod.UIAInspector()
    win, tops = find_window(ins, args.hwnd, args.title)
    if win is None:
        print("!! no main window found; %d top-level windows visible" % len(tops))
        print("   a tiny number here usually means an elevation barrier "
              "(CLAUDE.md section 4)")
        return 2
    print("[window] %r hwnd=%d class=%s" % (win[0], win[1], win[2]))

    cond = ins._uia.CreatePropertyCondition(UIA_AutomationIdPropertyId, args.aid)
    combo = win[3].FindFirst(TreeScope_Subtree, cond)
    if not combo:
        print("!! no descendant with AutomationId=%r" % args.aid)
        return 2
    print("[combo ] ct=%s name=%r aid=%r"
          % (combo.CurrentControlType, str(combo.CurrentName),
             str(combo.CurrentAutomationId)))

    # What does the CLOSED combo publish? This is the 2026-09-01 measurement.
    n, rows = dump_items(ins, combo)
    print("[closed] ListItem descendants of the combo: %d %s" % (n, rows[:5]))
    n_win, _ = dump_items(ins, win[3])
    print("[closed] ListItem descendants of the whole main window: %d" % n_win)

    before = set(t[1] for t in tops)

    pat = combo.GetCurrentPattern(UIA_ExpandCollapsePatternId)
    if not pat:
        print("!! ExpandCollapsePattern not supported")
        return 2
    # QueryInterface, not ctypes.cast — same idiom agent.py uses (agent.py:1750).
    ecp = pat.QueryInterface(ins._mod.IUIAutomationExpandCollapsePattern)
    ecp.Expand()
    time.sleep(args.settle)
    print("[expand] ExpandCollapseState = %s" % ecp.CurrentExpandCollapseState)

    tops2 = top_level(ins._uia)
    new = [t for t in tops2 if t[1] not in before]
    print("[after ] top-level windows: %d -> %d, new: %d"
          % (len(tops), len(tops2), len(new)))
    for t in new:
        n2, rows2 = dump_items(ins, t[3])
        print("  NEW hwnd=%d(0x%X) class=%s name=%r  ListItems=%d"
              % (t[1], t[1], t[2], t[0], n2))
        for nm, aid in rows2:
            print("       - name=%r automationId=%r" % (nm, aid))

    # Is the item reachable from the MAIN window subtree while open?
    ncond = ins._uia.CreatePropertyCondition(UIA_NamePropertyId, args.item)
    hit = win[3].FindFirst(TreeScope_Subtree, ncond)
    print("[reach ] %r under MAIN window subtree while open: %s"
          % (args.item, "FOUND ct=%s" % hit.CurrentControlType if hit else "not found"))

    # Every filter osScopedInvoke.py's stage-(b) applies, measured one by one.
    # It walks top_windows() (EnumWindows + IsWindowVisible) and skips any
    # window whose PID differs from the main window's. If the dropdown window
    # fails either test, stage (b) can never reach it no matter what the
    # selector says.
    u32 = ctypes.windll.user32
    main_pid = ctypes.wintypes.DWORD()
    u32.GetWindowThreadProcessId(win[1], ctypes.byref(main_pid))
    enum_hwnds = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
    def _cb(h, _):
        if u32.IsWindowVisible(h):
            enum_hwnds.append(h)
        return True

    u32.EnumWindows(_cb, 0)
    print("[filters] main hwnd=%d pid=%d | EnumWindows+visible returns %d windows"
          % (win[1], main_pid.value, len(enum_hwnds)))
    for t in new:
        pid = ctypes.wintypes.DWORD()
        u32.GetWindowThreadProcessId(t[1], ctypes.byref(pid))
        print("  hwnd=0x%X  IsWindowVisible=%s  inEnumWindows=%s  pid=%d (main=%d, same=%s)"
              % (t[1], bool(u32.IsWindowVisible(t[1])), t[1] in enum_hwnds,
                 pid.value, main_pid.value, pid.value == main_pid.value))
        print("       GetParent=0x%X  GA_ROOT=0x%X  GW_OWNER=0x%X"
              % (u32.GetParent(t[1]) or 0, u32.GetAncestor(t[1], 2) or 0,
                 u32.GetWindow(t[1], 4) or 0))

    # The capture-side fix (agent.py, 2026-09-02): given the open list window
    # and the point the user clicked to open it, can we get back to the combo?
    # That click hit-tests to a ListItem, so this is the lookup that decides
    # whether the open click is recorded as a combo or lost.
    # Every ComboBox visible from the same process, with its rect, while the
    # list is open. This is what combo_under_dropdown() walks, so a point it
    # reports as "contained by nothing" can be checked directly against these.
    u32d = ctypes.windll.user32
    ppid = ctypes.wintypes.DWORD()
    u32d.GetWindowThreadProcessId(win[1], ctypes.byref(ppid))
    print("[combos] every ComboBox in same-PID windows while the list is open:")
    for t2 in top_level(ins._uia):
        p2 = ctypes.wintypes.DWORD()
        u32d.GetWindowThreadProcessId(t2[1], ctypes.byref(p2))
        if p2.value != ppid.value:
            continue
        print("   -- window 0x%X cls=%s" % (t2[1], t2[2]))
        try:
            arr = t2[3].FindAll(TreeScope_Subtree, ins._uia.CreatePropertyCondition(
                UIA_ControlTypePropertyId, 50003))
        except Exception:
            continue
        for i in range(arr.Length):
            c = arr.GetElement(i)
            try:
                cr2 = c.CurrentBoundingRectangle
                ec = bool(c.GetCurrentPattern(10005))
                print("   win=0x%-8X cls=%-32s aid=%-16r rect=(%d,%d,%d,%d) ec=%s"
                      % (t2[1], t2[2], str(c.CurrentAutomationId), cr2.left,
                         cr2.top, cr2.right, cr2.bottom, ec))
            except Exception as e:
                print("   win=0x%-8X <unreadable: %s>" % (t2[1], e))

    if args.point:
        px, py = [int(v) for v in args.point.split(",")]
        for t in new:
            found = ins.combo_under_dropdown(t[1], px, py, set())
            print("[reattr] --point (%d,%d) popup=0x%X -> %s"
                  % (px, py, t[1],
                     ("aid=%r" % str(found.CurrentAutomationId)) if found else "None"))

    for t in new:
        try:
            cr = combo.CurrentBoundingRectangle
        except Exception:
            break
        px, py = (cr.left + cr.right) // 2, (cr.top + cr.bottom) // 2
        found = ins.combo_under_dropdown(t[1], px, py, set())
        if found:
            print("[reattr] combo_under_dropdown(popup=0x%X, pt=(%d,%d)) -> "
                  "aid=%r name=%r ct=%s"
                  % (t[1], px, py, str(found.CurrentAutomationId),
                     str(found.CurrentName), found.CurrentControlType))
            print("[reattr] MATCHES the combo we expanded: %s"
                  % (str(found.CurrentAutomationId) == args.aid))
        else:
            print("[reattr] combo_under_dropdown(popup=0x%X, pt=(%d,%d)) -> None "
                  "— the open click would still be lost" % (t[1], px, py))

    with open(HANDOFF, "w") as f:
        json.dump({"hwnd": win[1], "new": [t[1] for t in new]}, f)
    print("[handoff] wrote %s" % HANDOFF)
    return 0


def phase_find(args):
    """What osScopedInvoke.py does at step 14, in its own fresh process."""
    ins = agentmod.UIAInspector()
    hwnd = args.hwnd
    prev_new = []
    if not hwnd and os.path.exists(HANDOFF):
        d = json.load(open(HANDOFF))
        hwnd = d.get("hwnd")
        prev_new = d.get("new", [])
    win, tops = find_window(ins, hwnd, args.title)
    if win is None:
        print("!! no main window found")
        return 2
    print("[window] %r hwnd=%d" % (win[0], win[1]))
    print("[tops  ] %d top-level windows now" % len(tops))
    live = set(t[1] for t in tops)
    for h in prev_new:
        print("[gone? ] dropdown window 0x%X from the expand phase: %s"
              % (h, "STILL ALIVE" if h in live else "GONE"))

    ncond = ins._uia.CreatePropertyCondition(UIA_NamePropertyId, args.item)

    hit = win[3].FindFirst(TreeScope_Subtree, ncond)
    print("[stage1] %r under main window subtree: %s"
          % (args.item, "FOUND ct=%s" % hit.CurrentControlType if hit else "not found"))

    found_any = False
    for t in tops:
        if t[1] == win[1]:
            continue
        h2 = t[3].FindFirst(TreeScope_Subtree, ncond)
        if h2:
            found_any = True
            print("[stage2] %r FOUND in hwnd=%d(0x%X) class=%s name=%r ct=%s"
                  % (args.item, t[1], t[1], t[2], t[0], h2.CurrentControlType))
    if not found_any:
        print("[stage2] %r not found under ANY other top-level window" % args.item)
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hwnd", type=int, default=0)
    ap.add_argument("--title", default="Medflow Clinical System")
    ap.add_argument("--aid", default="deptSelect")
    ap.add_argument("--item", default="Pediatrics")
    ap.add_argument("--settle", type=float, default=0.8)
    ap.add_argument("--point", default="",
                    help="x,y to test combo_under_dropdown against directly")
    ap.add_argument("--phase", choices=["expand", "find", "both"], default="both")
    ap.add_argument("--launch", action="store_true")
    ap.add_argument("--keep", action="store_true",
                    help="leave the launched app running")
    args = ap.parse_args()

    agentmod._enable_per_monitor_dpi_awareness()

    proc = None
    if args.launch:
        proc = subprocess.Popen(["mshta.exe", os.path.abspath(HTA)])
        time.sleep(4)

    try:
        if args.phase == "expand":
            return phase_expand(args)
        if args.phase == "find":
            return phase_find(args)

        print("=" * 72)
        print("PHASE 1 - expand, in a SEPARATE process that then EXITS")
        print("          (this is osExpandCollapse.py at step 13)")
        print("=" * 72)
        cmd = [sys.executable, os.path.abspath(__file__), "--phase", "expand",
               "--aid", args.aid, "--item", args.item, "--title", args.title,
               "--settle", str(args.settle)]
        if args.hwnd:
            cmd += ["--hwnd", str(args.hwnd)]
        rc = subprocess.call(cmd)
        if rc != 0:
            return rc
        print("")
        print("=" * 72)
        print("PHASE 2 - fresh process, search for the item")
        print("          (this is osScopedInvoke.py at step 14)")
        print("=" * 72)
        return phase_find(args)
    finally:
        if proc is not None and not args.keep:
            try:
                proc.terminate()
            except Exception:
                pass


if __name__ == "__main__":
    sys.exit(main())
