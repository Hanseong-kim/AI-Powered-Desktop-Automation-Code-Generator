r"""
================================================================================
  Offscreen Hit-Test Probe
  Read-only diagnostic for "the captured element is not the one the user
  clicked, and the one that was captured is clipped out of view".
================================================================================

WHAT THIS IS FOR

    2026-09-01, Medflow HTA re-recording. The user clicked the SETTINGS button
    in the footer (the click worked — the Settings window opened), but the
    capture recorded:

        name="Department"  controlType=DataItem(50029)  automationId=""
        rect=(1575,989,1695,1007)   click point=(1680,991)

    The click point really is inside that rect, so every containment guard in
    agent.py accepts it. "Department" is not in the footer at all — it is a
    <td class='kvKey'> in the right-hand blue panel, inside #detailsBox, which
    is a FIXED-HEIGHT box with clipped overflow. The hypothesis this script
    tests:

      H1 (offscreen overlap): #detailsBox's content overflows its clip box, so
         rows past the fold keep real layout coordinates that spill down over
         the footer's screen area. MSHTML's own hit-testing honours the clip
         (which is why the physical click reached SETTINGS), but UIA reports
         those clipped elements with their unclipped BoundingRectangle and
         flags them IsOffscreen=True. agent.py never reads IsOffscreen (0
         occurrences, measured 2026-09-01 — while server.js's replay helpers
         check CurrentIsOffscreen in 4 places), so smallest_element_at() picks
         the invisible cell over the real button because it is smaller.

      H2 (layout overlap): the right panel genuinely overlaps the footer on
         screen and both are visible. Then IsOffscreen would be False for the
         captured cell and the bug is in MedflowMain.hta's layout(), not in
         agent.py.

    The two are told apart by one column of this script's output: IsOffscreen
    on the element that contains the click point and is smaller than the
    button.

HOW TO RUN IT

    The target app must be inspected at the same integrity level it runs at.
    Medflow is launched BY agent.py, which is elevated, so this must run from
    an ELEVATED PowerShell or it will see a 2-element stub tree (CLAUDE.md §4).

        python poc\diag_offscreen_hittest.py --title "Medflow Clinical System" --x 1680 --y 991

    Clicks nothing, changes nothing. Only reads UIA properties.
"""

import argparse
import sys

import comtypes.client
from comtypes.gen import UIAutomationClient as UIA

# The ids agent.py logs, so this output reads next to a capture log.
CT_NAMES = {
    50000: "Button", 50001: "Calendar", 50002: "CheckBox", 50003: "ComboBox",
    50004: "Edit", 50005: "Hyperlink", 50006: "Image", 50007: "ListItem",
    50008: "List", 50009: "Menu", 50010: "MenuBar", 50011: "MenuItem",
    50012: "ProgressBar", 50013: "RadioButton", 50014: "ScrollBar",
    50015: "Slider", 50016: "Spinner", 50017: "StatusBar", 50018: "Tab",
    50019: "TabItem", 50020: "Text", 50021: "ToolBar", 50022: "ToolTip",
    50023: "Tree", 50024: "TreeItem", 50025: "Custom", 50026: "Group",
    50027: "Thumb", 50028: "DataGrid", 50029: "DataItem", 50030: "Document",
    50031: "SplitButton", 50032: "Window", 50033: "Pane", 50034: "Header",
    50035: "HeaderItem", 50036: "Table", 50037: "TitleBar", 50038: "Separator",
}


def ct_name(cid):
    return CT_NAMES.get(cid, str(cid))


def safe(fn, default=""):
    try:
        return fn()
    except Exception as e:
        return f"ERR:{type(e).__name__}"


def rect_of(el):
    try:
        r = el.CurrentBoundingRectangle
        return (r.left, r.top, r.right, r.bottom)
    except Exception as e:
        return f"ERR:{type(e).__name__}"


def contains(rect, x, y):
    # Win32/UIA rect semantics: right/bottom are EXCLUSIVE (agent.py's
    # point_in_rect uses the same rule).
    if not isinstance(rect, tuple):
        return False
    return rect[0] <= x < rect[2] and rect[1] <= y < rect[3]


def top_level_windows(uia):
    """Every direct child of the UIA root, with the facts needed to pick one.

    Deliberately returns ALL of them rather than filtering: a window whose
    UIA Name does not match what the recording captured is itself a finding
    (mshta's top-level HTML Application Host Window Class does not always
    carry the document title), and a near-empty list is the elevation
    barrier (CLAUDE.md §4).
    """
    root = uia.GetRootElement()
    walker = uia.ControlViewWalker
    child = walker.GetFirstChildElement(root)
    out = []
    while child:
        out.append({
            "el": child,
            "name": str(safe(lambda c=child: c.CurrentName)),
            "cls": str(safe(lambda c=child: c.CurrentClassName)),
            "hwnd": safe(lambda c=child: c.CurrentNativeWindowHandle, 0),
            "rect": rect_of(child),
        })
        child = walker.GetNextSiblingElement(child)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--title", default="", help="window title substring")
    ap.add_argument("--hwnd", type=int, default=0,
                    help="attach by native window handle instead of title")
    ap.add_argument("--x", type=int, default=None)
    ap.add_argument("--y", type=int, default=None)
    ap.add_argument("--aid", default="",
                    help="instead of --x/--y, locate this AutomationId inside "
                         "the chosen window and probe its centre point — the "
                         "point a user aiming at that control would hit")
    args = ap.parse_args()
    if not args.aid and (args.x is None or args.y is None):
        ap.error("give either --x/--y or --aid")

    uia = comtypes.client.CreateObject(
        "{ff48dba4-60ef-4201-aa87-54103eef594e}",
        interface=UIA.IUIAutomation,
    )

    # --- Pick the window --------------------------------------------------
    tops = top_level_windows(uia)
    print(f"[top-level] {len(tops)} windows under the UIA root")
    for t in tops:
        mark = ""
        if args.title and args.title.lower() in t["name"].lower():
            mark = "  <== title match"
        if args.hwnd and t["hwnd"] == args.hwnd:
            mark = "  <== hwnd match"
        if args.x is not None and contains(t["rect"], args.x, args.y):
            mark += "  [contains point]"
        print(f"    hwnd={t['hwnd']:<10} cls={t['cls'][:34]:<34} "
              f"rect={str(t['rect']):<26} name={t['name'][:40]!r}{mark}")
    print()

    win = None
    if args.hwnd:
        win = next((t for t in tops if t["hwnd"] == args.hwnd), None)
    if win is None and args.title:
        win = next((t for t in tops
                    if args.title.lower() in t["name"].lower()), None)
    if win is None and args.x is not None:
        # Fall back to whichever top-level window actually covers the point —
        # that is the window the click went to, whatever it calls itself.
        cands = [t for t in tops if contains(t["rect"], args.x, args.y)]
        if cands:
            win = min(cands, key=lambda t: (t["rect"][2] - t["rect"][0])
                      * (t["rect"][3] - t["rect"][1]))
            print(f"[window] no name/hwnd match — falling back to the "
                  f"smallest top-level window containing the point: "
                  f"hwnd={win['hwnd']} name={win['name']!r}")
    if win is None:
        print("!! could not pick a window. If the list above is empty or tiny, "
              "you are not elevated (CLAUDE.md §4).")
        return 1

    print(f"[window] using hwnd={win['hwnd']} cls={win['cls']!r} "
          f"name={win['name']!r} rect={win['rect']}")
    win = win["el"]

    # --- Resolve the probe point -----------------------------------------
    if args.aid:
        cond_id = uia.CreatePropertyCondition(
            UIA.UIA_AutomationIdPropertyId, args.aid)
        target = win.FindFirst(UIA.TreeScope_Descendants, cond_id)
        if not target:            # comtypes returns a NULL pointer, not None
            print(f"!! no descendant with AutomationId={args.aid!r}")
            return 1
        trect = rect_of(target)
        if not isinstance(trect, tuple):
            print(f"!! AutomationId={args.aid!r} has no readable rect: {trect}")
            return 1
        args.x = (trect[0] + trect[2]) // 2
        args.y = (trect[1] + trect[3]) // 2
        print(f"[target ] AutomationId={args.aid!r} "
              f"ct={ct_name(safe(lambda: target.CurrentControlType, 0))} "
              f"name={safe(lambda: target.CurrentName)!r} rect={trect} "
              f"offscreen={safe(lambda: bool(target.CurrentIsOffscreen))}")

    print(f"[point  ] ({args.x},{args.y})")
    print()

    # --- What UIA's own hit test says -------------------------------------
    try:
        pt = UIA.tagPOINT(args.x, args.y)
        efp = uia.ElementFromPoint(pt)
        print("[ElementFromPoint] "
              f"name={safe(lambda: efp.CurrentName)!r} "
              f"ct={ct_name(safe(lambda: efp.CurrentControlType, 0))} "
              f"aid={safe(lambda: efp.CurrentAutomationId)!r} "
              f"rect={rect_of(efp)} "
              f"offscreen={safe(lambda: bool(efp.CurrentIsOffscreen))}")
    except Exception as e:
        print(f"[ElementFromPoint] failed: {e}")
    print()

    # --- Every descendant whose rect contains the point -------------------
    cond = uia.CreateTrueCondition()
    all_els = win.FindAll(UIA.TreeScope_Descendants, cond)
    total = all_els.Length
    rows = []
    for i in range(total):
        el = all_els.GetElement(i)
        r = rect_of(el)
        if not contains(r, args.x, args.y):
            continue
        area = (r[2] - r[0]) * (r[3] - r[1])
        rows.append({
            "area": area,
            "name": safe(lambda e=el: e.CurrentName),
            "ct": ct_name(safe(lambda e=el: e.CurrentControlType, 0)),
            "aid": safe(lambda e=el: e.CurrentAutomationId),
            "cls": safe(lambda e=el: e.CurrentClassName),
            "rect": r,
            "off": safe(lambda e=el: bool(e.CurrentIsOffscreen)),
        })

    rows.sort(key=lambda d: d["area"])
    print(f"[subtree] {total} descendants total, "
          f"{len(rows)} of them contain the point")
    print(f"{'area':>9}  {'offscr':>6}  {'ct':<12} {'aid':<16} {'name':<28} rect")
    print("-" * 110)
    for d in rows:
        print(f"{d['area']:>9}  {str(d['off']):>6}  {d['ct']:<12} "
              f"{str(d['aid'])[:16]:<16} {str(d['name'])[:28]:<28} {d['rect']}")

    # --- Is the winner even related to what the hit test returned? --------
    # agent.py's element_at() runs smallest_element_at() against the WINDOW
    # root, so the element it adopts need not be a descendant of the element
    # ElementFromPoint returned. Scoping the same search to the hit's own
    # subtree is the candidate fix; this measures what that would change.
    try:
        pt2 = UIA.tagPOINT(args.x, args.y)
        hit = uia.ElementFromPoint(pt2)
        sub = hit.FindAll(UIA.TreeScope_Subtree, cond)
        sub_rows = []
        for i in range(sub.Length):
            el = sub.GetElement(i)
            r = rect_of(el)
            if not contains(r, args.x, args.y):
                continue
            sub_rows.append({
                "area": (r[2] - r[0]) * (r[3] - r[1]),
                "name": safe(lambda e=el: e.CurrentName),
                "ct": ct_name(safe(lambda e=el: e.CurrentControlType, 0)),
                "aid": safe(lambda e=el: e.CurrentAutomationId),
                "rect": r,
            })
        sub_rows.sort(key=lambda d: d["area"])
        print(f"[subtree-scoped] hit test returned "
              f"aid={safe(lambda: hit.CurrentAutomationId)!r} "
              f"name={safe(lambda: hit.CurrentName)!r}; its OWN subtree has "
              f"{sub.Length} elements, {len(sub_rows)} containing the point")
        for d in sub_rows:
            print(f"    {d['area']:>9}  {d['ct']:<12} {str(d['aid'])[:16]:<16} "
                  f"{str(d['name'])[:28]:<28} {d['rect']}")
        if sub_rows:
            print(f"    -> subtree-scoped winner: {sub_rows[0]['ct']} "
                  f"aid={sub_rows[0]['aid']!r} name={sub_rows[0]['name']!r}")
    except Exception as e:
        print(f"[subtree-scoped] failed: {e}")

    print()
    visible = [d for d in rows if d["off"] is False]
    hidden = [d for d in rows if d["off"] is True]
    print(f"[verdict] containing the point: "
          f"{len(visible)} visible, {len(hidden)} offscreen")
    if rows:
        print(f"          smallest overall : {rows[0]['ct']} "
              f"aid={rows[0]['aid']!r} name={rows[0]['name']!r} "
              f"offscreen={rows[0]['off']}")
    if visible:
        print(f"          smallest VISIBLE : {visible[0]['ct']} "
              f"aid={visible[0]['aid']!r} name={visible[0]['name']!r}")
    if rows and visible and rows[0] is not visible[0]:
        print("          -> H1 CONFIRMED: the smallest element at this point is "
              "clipped out of view; filtering IsOffscreen picks the real one.")
    elif rows:
        print("          -> H1 NOT confirmed: the smallest element here is "
              "visible. Look at layout(), not at IsOffscreen.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
