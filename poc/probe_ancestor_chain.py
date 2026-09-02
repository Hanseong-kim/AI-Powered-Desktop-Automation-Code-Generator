"""
================================================================================
  Ancestor Chain Probe
  Read-only diagnostic for _ancestor_sibling_selector()'s "no direct children
  of controlType=X" failure (agent/agent.py, 2026-08-08 FileZilla SplitButton
  investigation).
================================================================================

WHAT THIS IS FOR

    agent.py's _ancestor_sibling_selector() finds the nearest named ancestor
    (ControlViewWalker) and then searches that ancestor's DIRECT children
    (TreeScope_Children) for a sibling of the same ControlType as the clicked
    element. For FileZilla's "사이트 관리자" SplitButton this returns nothing
    at every named ancestor up to the top of the tree, while the same logic
    works for its sibling CheckBoxes.

    Two hypotheses explain that symptom equally well from the recording log
    alone:

      H1 (app structure): the SplitButton really is nested one wrapper
         deeper than the CheckBoxes (e.g. inside an unnamed Pane/Group), so
         it is nobody's direct child at the level the walk finds.

      H2 (tree-view mismatch): climbing uses ControlViewWalker but descending
         uses FindAll(TreeScope_Children, ...), and if FindAll walks a
         different (e.g. raw) view than the walker does, a wrapper skipped on
         the way up reappears as a direct child on the way down, pushing the
         real target one level further away than the walk accounts for.

    This script does not click anything. It dumps enough of the live UIA
    tree around one point to tell the two apart by inspection, with the same
    controlType ids/names agent.py logs so the output reads next to a
    recording log line for line.


HOW TO RUN IT

    Point the app you want to inspect so the control in question is visible,
    then (from an elevated PowerShell if the app itself needs elevation —
    same rule as agent.py):

        python probe_ancestor_chain.py --title FileZilla --x 493 --y 124

    Coordinates are screen-absolute, same as what agent.py's [diag-click]
    log lines report as pynput_pt/cursor_pt — copy one straight from a
    recording log to reproduce exactly what agent.py saw.

    Run it once for the failing control and once for a working control (a
    sibling CheckBox, say) as a control group — the two outputs are meant to
    be diffed by eye.

    --before-after mode (added after a first pass found the ancestor's
    direct-children count differs depending on which control was
    hit-tested first): censuses the given ancestor's direct children BEFORE
    ever calling ElementFromPoint on (x, y), then hit-tests it, then
    censuses again — isolating whether hit-testing itself is what makes a
    control vanish from its parent's enumeration. Needs the ancestor's own
    identity (read off the plain-mode output above), e.g.:

        python probe_ancestor_chain.py --title FileZilla --exe filezilla.exe \
            --x 493 --y 124 --before-after \
            --ancestor-id 5999 --ancestor-class ToolbarWindow32

    --walker-vs-refetch mode (added after --before-after's own result showed
    UNCHANGED, but pointed at a DIFFERENT variable than hit-test order — see
    this script's own history in the project's daily notes): within a single
    run, climbs to the nearest named ancestor via ControlViewWalker starting
    from the hit-tested element itself (exactly what agent.py's
    _nearest_named_ancestor() does), censuses it, then re-resolves the same
    ancestor via a property-based FindFirst and censuses again. No
    --ancestor-* flags needed — it reads the ancestor's identity off the
    climbed element itself:

        python probe_ancestor_chain.py --title FileZilla --exe filezilla.exe \
            --x 493 --y 124 --walker-vs-refetch

IS IT SAFE?

    Read-only. Never calls Invoke/Toggle/SendInput or moves the mouse. Safe
    to run against a live app mid-recording (though simplest to run it
    standalone, app up, nothing being recorded).
================================================================================
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
        get_uia, describe, all_windows, pids_for_image, CONTROL_TYPE_NAMES,
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
# Default ctypes restype is c_int (32-bit) — on 64-bit Windows an HWND can
# exceed that range and silently truncate. Both calls below return HWNDs.
u.WindowFromPoint.restype = wintypes.HWND
u.GetAncestor.restype = wintypes.HWND
TreeScope_Children = 2

# ControlViewWalker/RawViewWalker/ContentViewWalker are properties on the
# IUIAutomation COM object, same names agent.py uses (agent.py:331 etc.).
WALKER_NAMES = ("RawViewWalker", "ControlViewWalker", "ContentViewWalker")


def ct_name(ct_id):
    return CONTROL_TYPE_NAMES.get(ct_id, str(ct_id))


def rects_match(a, b, tol=0):
    if not isinstance(a, tuple) or not isinstance(b, tuple):
        return a == b
    return all(abs(x - y) <= tol for x, y in zip(a, b))


# Terminal/console window classes — a title substring match can land on the
# very terminal this script is being run from (measured 2026-08-08: a
# PowerShell tab named "filezilla-splitbutton-diagnosis" for this debugging
# session matched --title FileZilla and, being nearly full-screen, out-sized
# the real FileZilla window under the old "pick the largest match"
# heuristic). Never auto-pick one of these.
TERMINAL_CLASSES = ("CASCADIA_HOSTING_WINDOW_CLASS", "ConsoleWindowClass",
                    "mintty", "VirtualConsoleClass")


def find_window(title_frag, exe=None):
    wins = [w for w in all_windows() if w["title"]]
    if exe:
        pids = pids_for_image(exe if exe.lower().endswith(".exe") else exe + ".exe")
        wins = [w for w in wins if w["pid"] in pids]
    if title_frag:
        q = title_frag.lower()
        wins = [w for w in wins if q in w["title"].lower()]
    if not wins:
        return None, []
    if len(wins) > 1:
        print(f"  ({len(wins)} windows matched — listing all, in case the wrong "
              f"one gets auto-picked):")
        for w in sorted(wins, key=lambda w: w["area"], reverse=True):
            print(f"    hwnd={w['hwnd']} pid={w['pid']} class={w['class']!r} "
                  f"area={w['area']} title={w['title']!r}")
    non_terminal = [w for w in wins if w["class"] not in TERMINAL_CLASSES]
    pool = non_terminal or wins
    pool.sort(key=lambda w: w["area"], reverse=True)
    return pool[0], wins


def bounding_rect(el):
    try:
        r = el.CurrentBoundingRectangle
        return (r.left, r.top, r.right, r.bottom)
    except Exception:
        return None


def describe_line(label, el):
    if el is None:
        print(f"  {label}: <none>")
        return
    d = describe(el)
    rect = bounding_rect(el)
    try:
        hwnd = el.CurrentNativeWindowHandle or 0
    except Exception:
        hwnd = 0
    print(f"  {label}: controlType={d['ctId']}({d['controlType']}) "
          f"name={d['name']!r} automationId={d['automationId']!r} "
          f"className={d['className']!r} rect={rect} hwnd={hwnd}")


def dump_walker_chain(uia, walker_name, elem, max_up=6):
    print(f"  -- {walker_name} --")
    try:
        walker = getattr(uia, walker_name)
    except Exception as e:
        print(f"     <could not get {walker_name}: {e}>")
        return
    cur = elem
    for hop in range(max_up):
        try:
            parent = walker.GetParentElement(cur)
        except Exception as e:
            print(f"     hop {hop}: GetParentElement raised: {e}")
            return
        if not parent:  # NULL COM pointer, not None — CLAUDE.md §5
            print(f"     hop {hop}: <top of tree>")
            return
        describe_line(f"hop {hop}", parent)
        cur = parent


def direct_children_census(uia, ancestor, quiet=False):
    """controlType histogram of ancestor's DIRECT children (TreeScope_Children
    — the same scope _find_sibling_by_controltype() in agent.py uses).
    Returns (total, {controlType: count}) so before/after callers can compare
    without re-printing; quiet=True suppresses the printed line."""
    try:
        arr = ancestor.FindAll(TreeScope_Children, uia.CreateTrueCondition())
    except Exception as e:
        if not quiet:
            print(f"     FindAll(Children) raised: {e}")
        return 0, {}
    if not arr or not arr.Length:
        if not quiet:
            print("     <no direct children>")
        return 0, {}
    counts = {}
    for i in range(arr.Length):
        try:
            ct = arr.GetElement(i).CurrentControlType
        except Exception:
            ct = -1
        counts[ct] = counts.get(ct, 0) + 1
    if not quiet:
        parts = ", ".join(f"{ct_name(ct)} x{n}" for ct, n in sorted(counts.items()))
        print(f"     direct children ({arr.Length} total): {parts}")
    return arr.Length, counts


def resolve_ancestor_by_props(uia, root_el, automation_id, class_name, name, ct_id=None):
    """Find one element under root_el by AND-ing whichever of
    automationId/className/name/controlType were given — same matching
    shape as server.js's resolve_cond()/field_conds() (AND-combine non-empty
    properties). Used by --before-after to locate the SAME ancestor
    (FileZilla's toolbar) WITHOUT ever hit-testing the target point first,
    so the "before" census is untainted by the very interaction being
    measured.

    ct_id (2026-08-09, sibling-count-drift investigation): a plain
    --ancestor-name match can land on the wrong element when the same text
    appears more than once in the window (e.g. "C:" in both the local tree
    and a remote pane) — className is not a safe extra filter here because
    these rows are owner-drawn and report className="" (className would be
    the CONTAINING control's class, not the row's), so controlType (already
    known from the recording log, e.g. 50024=TreeItem) is the reliable
    disambiguator instead."""
    conds = []
    if automation_id:
        conds.append(uia.CreatePropertyCondition(30011, automation_id))  # AutomationId
    if class_name:
        conds.append(uia.CreatePropertyCondition(30012, class_name))     # ClassName
    if name:
        conds.append(uia.CreatePropertyCondition(30005, name))           # Name
    if ct_id:
        conds.append(uia.CreatePropertyCondition(30003, ct_id))          # ControlType
    if not conds:
        print("  resolve_ancestor_by_props: no --ancestor-id/--ancestor-class/"
              "--ancestor-name/--ancestor-ct given")
        return None
    cond = conds[0]
    for c in conds[1:]:
        cond = uia.CreateAndCondition(cond, c)
    try:
        return root_el.FindFirst(7, cond)  # TreeScope_Subtree
    except Exception as e:
        print(f"  resolve_ancestor_by_props: FindFirst raised: {e}")
        return None


def run_before_after(uia, win, x, y, ancestor_id, ancestor_class, ancestor_name,
                      ancestor_ct=None):
    """Resolves the target ancestor (by AutomationId/className/name — NEVER
    by walking from the point) and censuses its direct children BEFORE any
    ElementFromPoint call touches (x, y), then does the ElementFromPoint hit
    -test, then censuses the SAME ancestor object again. This is the only
    way to tell H3-a (SplitButton was never a direct child, hit-testing is
    irrelevant) apart from H3-b (hit-testing the control makes it vanish
    from its parent's enumeration) — every prior probe run in this
    investigation hit-tested the point BEFORE censusing, so neither could be
    ruled out until now."""
    print(f"\n=== before/after: point ({x},{y}), ancestor "
          f"id={ancestor_id!r} class={ancestor_class!r} name={ancestor_name!r} "
          f"ct={ancestor_ct!r} ===")
    try:
        root_el = uia.ElementFromHandle(win["hwnd"])
    except Exception as e:
        print(f"  ElementFromHandle(hwnd={win['hwnd']}) raised: {e}")
        return
    ancestor = resolve_ancestor_by_props(uia, root_el, ancestor_id, ancestor_class,
                                          ancestor_name, ancestor_ct)
    if ancestor is None:
        print("  ancestor not found — check --ancestor-id/--ancestor-class/"
              "--ancestor-name/--ancestor-ct")
        return
    describe_line("ancestor", ancestor)

    print("  BEFORE (point not yet hit-tested):")
    before_total, before_counts = direct_children_census(uia, ancestor)

    pt = wintypes.POINT(int(x), int(y))
    try:
        hit_el = uia.ElementFromPoint(pt)
    except Exception as e:
        print(f"  ElementFromPoint raised: {e}")
        hit_el = None
    describe_line("hit-tested element", hit_el)

    print("  AFTER (same point just hit-tested via ElementFromPoint):")
    after_total, after_counts = direct_children_census(uia, ancestor)

    if before_total == after_total and before_counts == after_counts:
        print(f"  RESULT: UNCHANGED ({before_total} direct children both times) "
              "-> H3-a (the control was never enumerated as a direct child, "
              "hit-testing it made no difference)")
    else:
        print(f"  RESULT: *** CHANGED *** ({before_total} -> {after_total} direct "
              "children) -> H3-b (hit-testing the control via ElementFromPoint "
              "removed it from its parent's Children enumeration)")


def run_sample(uia, win, ancestor_id, ancestor_class, ancestor_name, ancestor_ct,
                samples, interval_ms):
    """Repeated-census mode (2026-08-09, sibling-count-drift investigation —
    FileZilla local tree/list children count recorded at capture time not
    matching what's found at replay time, e.g. 16 vs 0, 6 vs 26). Resolves
    the ancestor ONCE by property (never by hit-testing — no click/mouse
    involved at all) and censuses its direct children repeatedly over a
    short time window, with NO interaction from this script in between.

    This is designed to be started BEFORE the user performs the action in
    the GUI (expanding a tree node, etc.) — run with a generous --samples/
    --interval-ms so the whole window is sampled and whatever the user does
    partway through gets caught, rather than the script reacting to the
    action after the fact (human reaction time would eat a short async
    settle delay before the first sample ever ran).

    Distinguishes the competing hypotheses:
      - count never changes across all samples -> not a settle-timing race
        (contradicts H-비동기 지연 for this element); either the UI truly
        never populated those children (H-액션불일치) or they're gated on
        something this script's passive sampling can't trigger, like scroll
        position (H-가상화).
      - count changes partway through the sample window -> some async
        process filled in the children after a delay (H-비동기 지연,
        fixable with a short retry-with-delay loop on both the capture and
        replay sides).
    """
    print(f"\n=== sample: ancestor id={ancestor_id!r} class={ancestor_class!r} "
          f"name={ancestor_name!r} ct={ancestor_ct!r}, {samples} samples "
          f"@ {interval_ms}ms ===")
    try:
        root_el = uia.ElementFromHandle(win["hwnd"])
    except Exception as e:
        print(f"  ElementFromHandle(hwnd={win['hwnd']}) raised: {e}")
        return
    ancestor = resolve_ancestor_by_props(uia, root_el, ancestor_id, ancestor_class,
                                          ancestor_name, ancestor_ct)
    if ancestor is None:
        print("  ancestor not found — check --ancestor-id/--ancestor-class/"
              "--ancestor-name/--ancestor-ct")
        return
    describe_line("ancestor", ancestor)
    print("  (perform the GUI action now — sampling has already started)")

    prev = None
    for i in range(samples):
        total, _counts = direct_children_census(uia, ancestor, quiet=True)
        changed = " *** CHANGED ***" if prev is not None and total != prev else ""
        print(f"  sample {i:2d} (t={i * interval_ms:5d}ms): "
              f"{total} direct children{changed}")
        prev = total
        if i < samples - 1:
            time.sleep(interval_ms / 1000)


def climb_to_named_ancestor(uia, elem, max_up=4):
    """Mirrors agent.py's _nearest_named_ancestor() exactly (same walker,
    same stop condition, same NULL-COM-pointer-safe `not parent` check) —
    this is the ancestor-acquisition path _ancestor_sibling_selector()
    actually uses in production. Returns the ancestor element or None."""
    try:
        walker = uia.ControlViewWalker
    except Exception as e:
        print(f"  climb_to_named_ancestor: could not get ControlViewWalker: {e}")
        return None
    cur = elem
    for _hop in range(max_up):
        try:
            parent = walker.GetParentElement(cur)
        except Exception as e:
            print(f"  climb_to_named_ancestor: GetParentElement raised: {e}")
            return None
        if not parent:  # NULL COM pointer, not None — CLAUDE.md §5
            return None
        try:
            if parent.CurrentAutomationId or parent.CurrentName:
                return parent
        except Exception:
            return None
        cur = parent
    return None


def run_walker_vs_refetch(uia, win, x, y):
    """H4 confirmation: within ONE process/COM session, hit-test (x, y),
    climb to its nearest named ancestor via ControlViewWalker (exactly what
    agent.py's _nearest_named_ancestor() does), census that ancestor's
    direct children, then re-resolve the SAME ancestor by AutomationId/
    className/name via FindFirst (a "clean" reference untouched by the
    climb) and census again. Every earlier hint that these two acquisition
    paths give different Children enumerations came from comparing SEPARATE
    script runs (different point, different invocation) — this does both
    acquisitions back-to-back on the identical element within one run, so
    there is no room left for "something else changed between runs"."""
    print(f"\n=== walker-vs-refetch: point ({x},{y}) ===")
    try:
        root_el = uia.ElementFromHandle(win["hwnd"])
    except Exception as e:
        print(f"  ElementFromHandle(hwnd={win['hwnd']}) raised: {e}")
        return

    pt = wintypes.POINT(int(x), int(y))
    try:
        hit_el = uia.ElementFromPoint(pt)
    except Exception as e:
        print(f"  ElementFromPoint raised: {e}")
        return
    describe_line("hit-tested element", hit_el)
    if hit_el is None:
        return

    climbed = climb_to_named_ancestor(uia, hit_el)
    if climbed is None:
        print("  climb_to_named_ancestor: no named ancestor found — cannot compare")
        return
    describe_line("ancestor (via ControlViewWalker climb from hit-tested element)", climbed)
    print("  CLIMB-obtained ancestor's direct children:")
    climb_total, climb_counts = direct_children_census(uia, climbed)

    try:
        anc_id = climbed.CurrentAutomationId or ""
        anc_class = climbed.CurrentClassName or ""
        anc_name = climbed.CurrentName or ""
    except Exception as e:
        print(f"  could not read climbed ancestor's own identity: {e}")
        return
    refetched = resolve_ancestor_by_props(uia, root_el, anc_id, anc_class, anc_name)
    if refetched is None:
        print("  refetch via FindFirst failed — cannot compare")
        return
    describe_line("ancestor (re-fetched via FindFirst on its own AutomationId/"
                  "className/name)", refetched)
    print("  REFETCHED ancestor's direct children:")
    refetch_total, refetch_counts = direct_children_census(uia, refetched)

    if climb_total == refetch_total and climb_counts == refetch_counts:
        print(f"  RESULT: SAME (climb={climb_total}, refetch={refetch_total}) "
              "-> H4 NOT reproduced in this run")
    else:
        print(f"  RESULT: *** DIFFERENT *** (climb={climb_total}, "
              f"refetch={refetch_total}) -> H4 CONFIRMED — the ControlViewWalker"
              "-climbed ancestor reference omits the element it was climbed "
              "from from its own Children enumeration; a property-based "
              "refetch of the identical ancestor does not.")


def descendants_count_for_controltype(uia, ancestor, ct_id):
    """How many TreeScope_Descendants elements of ct_id sit under ancestor —
    tells us whether a Descendants fallback would even be safe (unique count
    == stable ordinal) without committing to using it."""
    TreeScope_Descendants = 4
    try:
        cond = uia.CreatePropertyCondition(30003, ct_id)  # UIA_ControlTypeProperty
        arr = ancestor.FindAll(TreeScope_Descendants, cond)
    except Exception as e:
        print(f"     FindAll(Descendants, controlType={ct_name(ct_id)}) raised: {e}")
        return
    n = arr.Length if arr else 0
    print(f"     Descendants with controlType={ct_name(ct_id)}: {n}")


def probe_point(uia, x, y, label):
    print(f"\n=== {label}: point ({x},{y}) ===")

    # 1. The two ways agent.py can end up with "the clicked element":
    #    raw ElementFromPoint, and smallest_element_at()'s picked element
    #    (smallest area among the window's full subtree containing the
    #    point — see agent.py smallest_element_at()). _ancestor_sibling_
    #    selector() only ever runs on the LATTER — see this script's module
    #    docstring for why the two must be checked for agreement before
    #    trusting any conclusion drawn from a single one of them.
    pt = wintypes.POINT(int(x), int(y))
    try:
        raw_el = uia.ElementFromPoint(pt)
    except Exception as e:
        print(f"  ElementFromPoint raised: {e}")
        raw_el = None
    describe_line("ElementFromPoint (raw)", raw_el)

    # smallest_element_at() needs a window root — resolve it the same way
    # agent.py's resolve_root_hwnd()/target windows do: the top-level window
    # under the point.
    win_hwnd = u.WindowFromPoint(pt)
    root_hwnd = win_hwnd
    # Walk up to the top-level owner (GA_ROOT = 2), same as agent.py's use
    # of GetAncestor for root resolution.
    GA_ROOT = 2
    try:
        top = u.GetAncestor(win_hwnd, GA_ROOT)
        if top:
            root_hwnd = top
    except Exception:
        pass
    try:
        root_el = uia.ElementFromHandle(root_hwnd)
    except Exception as e:
        print(f"  ElementFromHandle(root={root_hwnd}) raised: {e}")
        root_el = None

    smallest_el = None
    if root_el is not None:
        try:
            arr = root_el.FindAll(7, uia.CreateTrueCondition())  # TreeScope_Subtree
        except Exception as e:
            print(f"  root.FindAll(Subtree) raised: {e}")
            arr = None
        if arr and arr.Length:
            best_i, best_area = None, None
            for i in range(arr.Length):
                el = arr.GetElement(i)
                r = bounding_rect(el)
                if not r or r[2] <= r[0] or r[3] <= r[1]:
                    continue
                if not (r[0] <= x < r[2] and r[1] <= y < r[3]):
                    continue
                area = (r[2] - r[0]) * (r[3] - r[1])
                if best_area is None or area < best_area:
                    best_area, best_i = area, i
            if best_i is not None:
                smallest_el = arr.GetElement(best_i)
    describe_line("smallest_element_at (root subtree)", smallest_el)

    raw_rect = bounding_rect(raw_el) if raw_el is not None else None
    small_rect = bounding_rect(smallest_el) if smallest_el is not None else None
    if raw_el is None or smallest_el is None:
        print("  MATCH-CHECK: SKIPPED (one or both resolutions failed)")
        targets = [("smallest_element_at", smallest_el)] if smallest_el is not None else []
        if raw_el is not None:
            targets.append(("ElementFromPoint", raw_el))
    elif rects_match(raw_rect, small_rect) and (
            (describe(raw_el)["ctId"]) == (describe(smallest_el)["ctId"])):
        print("  MATCH-CHECK: MATCH (raw and smallest_element_at agree — safe "
              "to treat as one node below)")
        targets = [("smallest_element_at (== ElementFromPoint)", smallest_el)]
    else:
        print("  MATCH-CHECK: *** MISMATCH *** — raw and smallest_element_at "
              "picked DIFFERENT elements. agent.py's _ancestor_sibling_selector "
              "only ever walks from smallest_element_at's pick "
              "(picked_by=smallest_element_at in the recording log); dumping "
              "BOTH chains below so the real target can be identified instead "
              "of assumed.")
        targets = [("smallest_element_at", smallest_el), ("ElementFromPoint", raw_el)]

    for tname, el in targets:
        if el is None:
            continue
        d = describe(el)
        print(f"\n  --- ancestor chains for [{tname}] "
              f"(controlType={d['ctId']}={d['controlType']}) ---")
        for wname in WALKER_NAMES:
            dump_walker_chain(uia, wname, el)

        print(f"\n  --- direct-children census along ControlViewWalker chain "
              f"for [{tname}] ---")
        try:
            walker = uia.ControlViewWalker
        except Exception as e:
            print(f"     <could not get ControlViewWalker: {e}>")
            continue
        cur = el
        for hop in range(6):
            try:
                parent = walker.GetParentElement(cur)
            except Exception as e:
                print(f"     hop {hop}: GetParentElement raised: {e}")
                break
            if not parent:
                print(f"     hop {hop}: <top of tree>")
                break
            pd = describe(parent)
            named = bool(pd["automationId"] or pd["name"])
            print(f"   [hop {hop}] ancestor: id={pd['automationId']!r} "
                  f"name={pd['name']!r} controlType={pd['controlType']!r} "
                  f"{'(NAMED)' if named else '(unnamed)'}")
            if named:
                direct_children_census(uia, parent)
                descendants_count_for_controltype(uia, parent, d["ctId"])
            cur = parent


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--title", required=True, help="substring of the window title")
    ap.add_argument("--exe", default=None,
                     help="image name (e.g. filezilla.exe) to narrow --title matches "
                          "to windows owned by that process — use this if --title "
                          "alone matches more than one window (e.g. a terminal tab "
                          "that happens to contain the same text)")
    ap.add_argument("--x", type=int, default=None,
                     help="screen-absolute X (required except in --sample mode, "
                          "which never hit-tests a point)")
    ap.add_argument("--y", type=int, default=None,
                     help="screen-absolute Y (required except in --sample mode)")
    ap.add_argument("--label", default="probe", help="label for this point in the output")
    ap.add_argument("--before-after", action="store_true",
                     help="H3-a vs H3-b mode: census the ancestor's direct children "
                          "BEFORE ever hit-testing (x,y), then hit-test it, then "
                          "census again, and report whether the count changed. "
                          "Requires --ancestor-id and/or --ancestor-class (whichever "
                          "the earlier non-before-after run showed as the ancestor's "
                          "own identity, e.g. --ancestor-id 5999 --ancestor-class "
                          "ToolbarWindow32 for FileZilla's toolbar).")
    ap.add_argument("--ancestor-id", default="",
                     help="--before-after/--sample: ancestor's AutomationId")
    ap.add_argument("--ancestor-class", default="",
                     help="--before-after/--sample: ancestor's ClassName")
    ap.add_argument("--ancestor-name", default="",
                     help="--before-after/--sample: ancestor's Name")
    ap.add_argument("--ancestor-ct", type=int, default=None,
                     help="--before-after/--sample: ancestor's UIA ControlType id "
                          "(e.g. 50024=TreeItem, 50007=ListItem — read off the "
                          "recording log) — disambiguates when --ancestor-name "
                          "alone could match more than one element on screen; "
                          "className is NOT a safe extra filter for these owner"
                          "-drawn rows since they report className=\"\"")
    ap.add_argument("--sample", action="store_true",
                     help="Repeated-census mode (no hit-test, no interaction): "
                          "resolves the ancestor by property once, then censuses "
                          "its direct children --samples times, --interval-ms "
                          "apart, printing each count. Start this BEFORE doing "
                          "the GUI action (e.g. double-clicking a tree node) so "
                          "the whole sampling window covers it — see poc "
                          "docstring / project plan for the recommended "
                          "before-then-act ordering. Requires at least one of "
                          "--ancestor-id/--ancestor-class/--ancestor-name/"
                          "--ancestor-ct.")
    ap.add_argument("--samples", type=int, default=10,
                     help="--sample: number of census samples to take (default 10)")
    ap.add_argument("--interval-ms", type=int, default=300,
                     help="--sample: milliseconds between samples (default 300)")
    ap.add_argument("--walker-vs-refetch", action="store_true",
                     help="H4 confirmation mode: hit-test (x,y), climb to its nearest "
                          "named ancestor via ControlViewWalker (same path agent.py's "
                          "_nearest_named_ancestor() uses), census its direct children, "
                          "then re-resolve the SAME ancestor via a property-based "
                          "FindFirst and census again — all within one run, one COM "
                          "session. No --ancestor-* flags needed (the ancestor's "
                          "identity is read off the climbed element itself).")
    args = ap.parse_args()

    win, all_matches = find_window(args.title, exe=args.exe)
    if not win:
        print(f"No visible window matching title {args.title!r}"
              + (f" and exe {args.exe!r}" if args.exe else ""))
        sys.exit(1)
    if len(all_matches) > 1 and not args.exe:
        print(f"  -> auto-picked hwnd={win['hwnd']} (largest non-terminal match). "
              f"If this is wrong, re-run with --exe to pin it down precisely.")
    print(f"target window: hwnd={win['hwnd']} title={win['title']!r} "
          f"rect={win['rect']}")

    uia, _mod = get_uia()
    if args.sample:
        if not (args.ancestor_id or args.ancestor_class or args.ancestor_name
                or args.ancestor_ct):
            print("--sample requires at least one of --ancestor-id/"
                  "--ancestor-class/--ancestor-name/--ancestor-ct")
            sys.exit(1)
        run_sample(uia, win, args.ancestor_id, args.ancestor_class,
                   args.ancestor_name, args.ancestor_ct, args.samples,
                   args.interval_ms)
        return
    if args.x is None or args.y is None:
        print("--x/--y are required outside --sample mode")
        sys.exit(1)
    if args.walker_vs_refetch:
        run_walker_vs_refetch(uia, win, args.x, args.y)
    elif args.before_after:
        if not (args.ancestor_id or args.ancestor_class or args.ancestor_name
                or args.ancestor_ct):
            print("--before-after requires at least one of --ancestor-id/"
                  "--ancestor-class/--ancestor-name/--ancestor-ct")
            sys.exit(1)
        run_before_after(uia, win, args.x, args.y, args.ancestor_id,
                          args.ancestor_class, args.ancestor_name, args.ancestor_ct)
    else:
        probe_point(uia, args.x, args.y, args.label)


if __name__ == "__main__":
    main()
