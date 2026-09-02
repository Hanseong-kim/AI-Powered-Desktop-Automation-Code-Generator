"""
================================================================================
  Windows App Automatability Probe
  Tells you, in about 30 seconds, whether a desktop application can be driven
  by a UI Automation based test tool -- and if not, exactly why.
================================================================================

WHAT THIS IS FOR

    Windows automation tools (WinAppDriver, Appium, this recorder) find and
    click controls through the Windows UI Automation (UIA) API. That only works
    if the application publishes its controls to UIA. Many applications look
    completely native but publish little or nothing -- and there is no way to
    tell by looking at them.

    Rather than discovering that after building a test, point this script at the
    application first. Everything it prints is a direct measurement of that
    application, not an opinion.


HOW TO RUN IT

    Requirements
        - Windows
        - Python 3.9 or newer          (https://www.python.org/downloads/)
        - one package:   pip install comtypes

    Then pick whichever line is easiest:

        # 1. by executable -- launches the app if it is not already running
        python probe_app_automatability.py --exe "C:\\Path\\To\\YourApp.exe"

        # 2. by window title (a substring is fine)
        python probe_app_automatability.py --title "Mediflow"

        # 3. by process id, if the title is ambiguous
        python probe_app_automatability.py --pid 1234

    Optional: add   --json report.json   to also write the raw findings to a
    file, which is the most useful thing to send back.

    IMPORTANT: bring the application's window to the FRONT before running, and
    leave it in a normal, populated state (logged in, a typical screen open).
    Several UI frameworks publish nothing at all while their window is inactive,
    and an empty screen has no controls to measure. The script tries to focus the
    window itself, and says "INCONCLUSIVE" rather than guessing if it cannot.

    Run it once per major screen you care about (login, dashboard, a form). Tiers
    are a property of the controls on screen, not of the whole application.


IS IT SAFE?

    Yes. It is read-only. It queries the accessibility information Windows
    already exposes and moves the mouse cursor nowhere. It does not click,
    type, modify, save, or transmit anything. It needs no admin rights, makes no
    network connections, and writes nothing except the optional --json file you
    ask for.


WHAT THE RESULT MEANS

    The script ends with a VERDICT naming one of four tiers:

    TIER 1  Standard Windows controls (Win32 / WinForms / WPF / MFC).
            SUPPORTED. Everything on screen is addressable and actionable.
            Examples measured: PuTTY, Notepad.

    TIER 2  A custom UI framework that still implements accessibility properly.
            SUPPORTED, with a small per-framework adjustment.
            Examples measured: 7-Zip, FileZilla, HeidiSQL.

    TIER 3  Owner-drawn controls: the app paints the rows/cells itself and
            publishes no accessibility objects for them. The control is visible
            and populated on screen, yet automation sees an empty box.
            NOT AUTOMATABLE -- there is nothing to select.
            Example measured: HeidiSQL's saved-session list.

    TIER 4  Web content hosted inside a native window (Electron / CEF /
            WebView2). The window frame is native but the interface is a web
            page rendered by a browser engine, and it lives in a separate tree
            that the application window cannot reach.
            NOT AUTOMATABLE by a UI Automation based tool -- it needs a browser
            automation protocol (Chrome DevTools Protocol / WebDriver) instead.
            Examples measured: TeamViewer 15, Visual Studio Code.

    INCONCLUSIVE
            The window could not be brought to the foreground and published
            almost nothing, so any verdict would be a guess. Click the window
            yourself and run it again.

    A CAVEAT line may also appear, listing sizeable lists/trees/grids that
    published no items. Check those on screen: if one visibly contains rows, it
    is owner-drawn (Tier 3) and those rows cannot be clicked. If it merely looks
    empty at that moment, populate it and re-run.


WHAT TO SEND BACK

    The whole console output, or the --json file. The four numbered sections
    above the verdict carry the evidence:

      1. browser-engine child windows      -> the Tier 4 detector
      2. reachability: how many elements are reachable by searching DOWN from
         the application window (what test REPLAY uses) versus by hit-testing a
         point (what RECORDING uses). A large gap between the two is the precise
         signature of "the recording works but the replay does nothing".
      3. containers that publish no items  -> possible owner-drawn controls
      4. every interactive control: whether it has a usable identifier, and
         which UIA patterns it actually supports (Invoke / Toggle /
         SelectionItem / ExpandCollapse / Value / Legacy). A control with no
         actionable pattern cannot be driven without clicking raw pixels.
"""

import argparse
import ctypes
import json
import subprocess
import sys
import time
from ctypes import wintypes

if sys.platform != "win32":
    print("This probe reads the Windows UI Automation API and only runs on "
          "Windows.")
    sys.exit(1)

try:
    import comtypes
    import comtypes.client
except ImportError:
    print("Missing the one dependency this script needs.\n")
    print("    pip install comtypes\n")
    print("Then run this script again. Nothing else is required.")
    sys.exit(1)

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Physical pixels, matching agent.py -- otherwise UIA rects and screen points
# disagree on any scaled display.
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

u = ctypes.windll.user32
TreeScope_Subtree = 7

PATTERN_IDS = {
    "Invoke": 10000,
    "Toggle": 10015,
    "SelectionItem": 10010,
    "ExpandCollapse": 10005,
    "Value": 10002,
    "Legacy": 10018,
    "Scroll": 10004,
}

CONTROL_TYPE_NAMES = {
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

# Controls a user actually clicks/types into.
INTERACTIVE = {50000, 50002, 50003, 50004, 50007, 50011, 50013, 50019,
               50024, 50029, 50031}
# Containers that are supposed to publish children. Zero children == owner-drawn.
CONTAINERS = {50003: "ComboBox", 50008: "List", 50018: "Tab", 50023: "Tree",
              50028: "DataGrid", 50036: "Table"}

# Win32 child-window classes that mean "a browser engine renders this UI".
CHROMIUM_CLASSES = ("Chrome_WidgetWin_", "Chrome_RenderWidgetHostHWND",
                    "WebView2", "TV_WebView2Control", "Intermediate D3D Window",
                    "CefBrowserWindow", "AtlAxWin")


# ─────────────────────────────────────────────────────────── window discovery
def all_windows():
    res = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def cb(h, _):
        if u.IsWindowVisible(h):
            t = ctypes.create_unicode_buffer(512)
            u.GetWindowTextW(h, t, 512)
            c = ctypes.create_unicode_buffer(256)
            u.GetClassNameW(h, c, 256)
            pid = wintypes.DWORD()
            u.GetWindowThreadProcessId(h, ctypes.byref(pid))
            r = wintypes.RECT()
            u.GetWindowRect(h, ctypes.byref(r))
            area = (r.right - r.left) * (r.bottom - r.top)
            res.append({"hwnd": h, "title": t.value, "class": c.value,
                        "pid": pid.value, "rect": (r.left, r.top, r.right, r.bottom),
                        "area": area})
        return True

    u.EnumWindows(cb, 0)
    return res


def pids_for_image(image_name):
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq %s" % image_name,
                          "/FO", "CSV", "/NH"], capture_output=True, text=True).stdout
    pids = set()
    for line in out.splitlines():
        p = [x.strip('" ') for x in line.split('","')]
        if len(p) >= 2:
            try:
                pids.add(int(p[1]))
            except ValueError:
                pass
    return pids


def child_windows(hwnd):
    got = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def cb(h, _):
        b = ctypes.create_unicode_buffer(256)
        u.GetClassNameW(h, b, 256)
        got.append((h, b.value))
        return True

    u.EnumChildWindows(hwnd, cb, 0)
    return got


def activate(hwnd):
    u.ShowWindow(hwnd, 9)  # SW_RESTORE
    cur = ctypes.windll.kernel32.GetCurrentThreadId()
    tgt = u.GetWindowThreadProcessId(hwnd, None)
    u.AttachThreadInput(cur, tgt, True)
    u.BringWindowToTop(hwnd)
    u.SetForegroundWindow(hwnd)
    u.AttachThreadInput(cur, tgt, False)
    time.sleep(1.5)
    return u.GetForegroundWindow() == hwnd


# ─────────────────────────────────────────────────────────────── UIA helpers
def get_uia():
    comtypes.CoInitialize()
    mod = comtypes.client.GetModule("UIAutomationCore.dll")
    uia = comtypes.client.CreateObject(
        "{ff48dba4-60ef-4201-aa87-54103eef594e}", interface=mod.IUIAutomation)
    return uia, mod


def patterns_of(el):
    got = []
    for label, pid in PATTERN_IDS.items():
        try:
            if el.GetCurrentPattern(pid):
                got.append(label)
        except Exception:
            pass
    return got


def describe(el):
    def safe(fn, d=""):
        try:
            return fn()
        except Exception:
            return d

    return {
        "controlType": CONTROL_TYPE_NAMES.get(safe(lambda: el.CurrentControlType, 0),
                                              str(safe(lambda: el.CurrentControlType, "?"))),
        "ctId": safe(lambda: el.CurrentControlType, 0),
        "name": safe(lambda: el.CurrentName or ""),
        "automationId": safe(lambda: el.CurrentAutomationId or ""),
        "className": safe(lambda: el.CurrentClassName or ""),
    }


def find_all(uia, root):
    try:
        arr = root.FindAll(TreeScope_Subtree, uia.CreateTrueCondition())
        return [arr.GetElement(i) for i in range(arr.Length)]
    except Exception:
        return []


def find_all_settled(uia, root, timeout=8.0, quiet_for=1.5):
    """find_all(), but wait until the subtree stops growing.

    A WebView2/Electron app publishes its accessibility tree progressively.
    Measured 2026-08-03 (TeamViewer 15.79), same window sampled repeatedly
    from a cold start:

        t=0s   t=1s   t=3s   t=7s
          25     26     61     61

    Sampling once gives a number that describes the probe's timing, not the
    app. That single mistake is how this tool produced a Tier 4 "not
    automatable" verdict for TeamViewer on 2026-07-31 — a verdict that did not
    survive re-measurement. Keep polling until the count has held still for
    `quiet_for` seconds, then report the settled tree.
    """
    best, stable_since = [], None
    deadline = time.time() + timeout
    while True:
        cur = find_all(uia, root)
        if len(cur) > len(best):
            best, stable_since = cur, time.time()
        elif stable_since is None:
            stable_since = time.time()
        if time.time() - stable_since >= quiet_for or time.time() >= deadline:
            return best
        time.sleep(0.3)


def hit_test_grid(uia, rect, steps=9):
    """What recording (ElementFromPoint) can see. Read-only, no input.

    Also counts how many probe points each element absorbed. A control that
    swallows many well-separated points while covering a large area is drawing
    its own content: the rows/cells a user sees are not UIA elements at all.
    That is the only way to detect a fully owner-drawn control, because such a
    control publishes nothing for a tree walk to find.
    """
    left, top, right, bottom = rect
    w, h = right - left, bottom - top
    seen = {}
    if w <= 4 or h <= 4:
        return seen
    for iy in range(1, steps + 1):
        for ix in range(1, steps + 1):
            px = int(left + w * ix / (steps + 1))
            py = int(top + h * iy / (steps + 1))
            try:
                el = uia.ElementFromPoint(wintypes.POINT(px, py))
            except Exception:
                continue
            if not el:
                continue
            d = describe(el)
            key = (d["controlType"], d["name"], d["automationId"], d["className"])
            if key in seen:
                seen[key]["hitPoints"] += 1
                continue
            try:
                r = el.CurrentBoundingRectangle
                d["area"] = (r.right - r.left) * (r.bottom - r.top)
                d["dims"] = "%dx%d" % (r.right - r.left, r.bottom - r.top)
            except Exception:
                d["area"], d["dims"] = 0, "?"
            d["hitPoints"] = 1
            seen[key] = d
    return seen


# ────────────────────────────────────────────────────────────────── the probe
def probe(hwnd, win_info):
    uia, mod = get_uia()
    report = {"window": win_info, "signals": {}}

    print("=" * 78)
    print("  APP AUTOMATABILITY PROBE")
    print("=" * 78)
    print("window : hwnd=%d  class=%r  title=%r" % (hwnd, win_info["class"], win_info["title"]))
    print("process: pid=%d" % win_info["pid"])

    fg = activate(hwnd)
    print("active : %s%s" % (fg, "" if fg else "   <-- WARNING: could not focus; "
                                                "some frameworks publish nothing while inactive"))
    print()

    # ---- signal 1: browser engine children --------------------------------
    kids = child_windows(hwnd)
    chromium = [(h, c) for h, c in kids
                if any(c.startswith(m) for m in CHROMIUM_CLASSES)]
    report["signals"]["chromiumChildren"] = [{"hwnd": h, "class": c} for h, c in chromium]

    print("--- 1. browser-engine child windows -----------------------------------")
    if chromium:
        print("  FOUND %d -- this app renders its UI with a browser engine:" % len(chromium))
        for h, c in chromium:
            print("     %-32s hwnd=%d" % (c, h))
    else:
        print("  none -- the UI is drawn by native controls")
    print()

    # ---- signal 2: downward reach vs hit-test reach ------------------------
    win = uia.ElementFromHandle(hwnd)
    # Settled, not single-shot — see find_all_settled(). A WebView2 app reads
    # as nearly empty for the first ~3s after launch.
    downward = find_all_settled(uia, win)
    print("--- 2. reachability -----------------------------------------------------")
    print("  downward from the app window (what REPLAY uses) : %d elements" % len(downward))

    # 2026-08-05 (Everything false-Tier-4 실측): win_info["rect"]는 activate()
    # 호출 전, 즉 창이 최소화/트레이 상태였을 수 있는 시점에 찍힌 좌표다.
    # activate()가 SW_RESTORE + SetForegroundWindow로 창을 실제로 복원한
    # "뒤"에도 이 낡은 rect를 그대로 히트테스트에 쓰면, 최소화 창의 화면-밖
    # 좌표(예: (-32000,-32000,...))로 격자를 찍어 완전히 무관한 창(이 도구
    # 자신의 Chrome 대시보드)을 히트테스트하게 된다 — 실측: Everything을
    # 이 상태로 찔렀더니 hit-test 결과가 'Captured Events (19)'/'TeamViewer'
    # 등 Code Generator 자신의 UI 요소로 나왔다. activate() 직후 rect를
    # 다시 읽어 실제 복원된 위치/크기를 쓴다.
    live_rect = win_info["rect"]
    try:
        r = wintypes.RECT()
        u.GetWindowRect(hwnd, ctypes.byref(r))
        live_rect = (r.left, r.top, r.right, r.bottom)
        if live_rect != win_info["rect"]:
            print(f"  [rect] refreshed after activate(): {win_info['rect']} -> {live_rect}")
    except Exception:
        pass

    hits = hit_test_grid(uia, live_rect)
    print("  by hit-test        (what RECORDING uses)         : %d distinct elements"
          % len(hits))

    # elements the hit test found that the downward walk did not
    down_keys = set()
    for el in downward:
        d = describe(el)
        down_keys.add((d["controlType"], d["name"], d["automationId"], d["className"]))
    only_hit = [d for k, d in hits.items() if k not in down_keys]
    report["signals"]["downwardCount"] = len(downward)
    report["signals"]["hitTestCount"] = len(hits)
    report["signals"]["hitTestOnly"] = only_hit

    if only_hit:
        print("  *** %d element(s) are visible to hit-test but NOT reachable downward:"
              % len(only_hit))
        for d in only_hit[:8]:
            print("        %-12s name=%r aid=%r" % (d["controlType"], d["name"][:30],
                                                    d["automationId"][:20]))
        print("      -> this is the 'records fine, replays as a no-op' signature")

    # if the app is Chromium, show what the renderer child exposes
    renderer_best = 0
    for h, c in chromium:
        try:
            n = len(find_all(uia, uia.ElementFromHandle(h)))
        except Exception:
            n = 0
        renderer_best = max(renderer_best, n)
    if chromium:
        print("  reachable from the renderer child hwnd           : %d elements"
              % renderer_best)
        report["signals"]["rendererCount"] = renderer_best
    print()

    # ---- signal 3: owner-drawn containers ----------------------------------
    print("--- 3. containers exposing no items (possible owner-drawn) --------------")
    # The only signal that holds up is: a sizeable list/tree/grid whose subtree
    # contains ZERO item-type descendants (ListItem/TreeItem/DataItem/TabItem).
    #
    # Two other heuristics were tried and REJECTED (measured 2026-07-31):
    #  - "zero children of any kind": a COLLAPSED ComboBox legitimately has none.
    #  - "many hit-test points collapse onto the container": a sparsely populated
    #    list has a lot of empty area, and hit-testing empty area correctly
    #    returns the container. 7-Zip's SysListView32 exposes 4 real ListItems
    #    ('컬퓨터','문서','네트워크','\\.') yet absorbed 76 probe points,
    #    which made a perfectly automatable control look owner-drawn.
    #
    # Even the item-count test cannot separate "owner-drawn" from "genuinely
    # empty right now" (FileZilla's file lists are empty until you connect), so
    # this section REPORTS suspects for a human to confirm on screen. It never
    # decides a tier by itself.
    MIN_AREA = 15000  # px^2 -- far larger than any collapsed combo
    item_cond = uia.CreateOrCondition(
        uia.CreatePropertyCondition(30003, 50007),           # ListItem
        uia.CreateOrCondition(
            uia.CreatePropertyCondition(30003, 50024),       # TreeItem
            uia.CreateOrCondition(
                uia.CreatePropertyCondition(30003, 50029),   # DataItem
                uia.CreatePropertyCondition(30003, 50019)))) # TabItem

    suspects = []
    collapsed_combos = []
    for el in downward:
        d = describe(el)
        if d["ctId"] not in CONTAINERS:
            continue
        try:
            r = el.CurrentBoundingRectangle
            area = (r.right - r.left) * (r.bottom - r.top)
            d["dims"] = "%dx%d" % (r.right - r.left, r.bottom - r.top)
        except Exception:
            area, d["dims"] = 0, "?"
        d["area"] = area
        try:
            d["items"] = el.FindAll(TreeScope_Subtree, item_cond).Length
        except Exception:
            d["items"] = -1
        if d["items"] > 0:
            continue
        if d["ctId"] == 50003 and area < MIN_AREA:
            collapsed_combos.append(d)
            continue
        if area < MIN_AREA:
            continue
        suspects.append(d)
        print("  NO ITEMS  %-10s %-9s name=%r cls=%r"
              % (d["controlType"], d["dims"], d["name"][:22], d["className"][:22]))

    if suspects:
        print("  -> CHECK ON SCREEN: if one of these visibly contains rows, it is")
        print("     owner-drawn and its rows cannot be clicked. If it merely looks")
        print("     empty right now, populate it and re-run.")
    else:
        print("  none -- every sizeable list/tree/grid exposes its items")
    if collapsed_combos:
        print("  (%d collapsed ComboBox(es) not assessed -- expand them and re-run)"
              % len(collapsed_combos))
    report["signals"]["noItemContainers"] = suspects
    report["signals"]["collapsedCombos"] = collapsed_combos
    print()

    # ---- signal 4: are interactive controls addressable AND actionable -----
    print("--- 4. interactive controls: addressable? actionable? -------------------")
    rows = []
    no_id = 0
    no_action = 0
    for el in downward:
        d = describe(el)
        if d["ctId"] not in INTERACTIVE:
            continue
        pats = patterns_of(el)
        actionable = bool({"Invoke", "Toggle", "SelectionItem",
                           "ExpandCollapse", "Value", "Legacy"} & set(pats))
        addressable = bool(d["automationId"] or d["className"] or d["name"])
        if not addressable:
            no_id += 1
        if not actionable:
            no_action += 1
        d["patterns"] = pats
        d["addressable"] = addressable
        d["actionable"] = actionable
        rows.append(d)

    if not rows:
        print("  NO interactive controls reachable at all")
    else:
        print("  %d interactive control(s); %d with no usable identifier; "
              "%d with no actionable pattern" % (len(rows), no_id, no_action))
        for d in rows[:25]:
            flag = "" if (d["addressable"] and d["actionable"]) else "   <-- PROBLEM"
            print("    %-12s name=%-24r aid=%-14r patterns=%s%s"
                  % (d["controlType"], d["name"][:24], d["automationId"][:14],
                     ",".join(d["patterns"]) or "NONE", flag))
        if len(rows) > 25:
            print("    ... %d more" % (len(rows) - 25))
    report["signals"]["interactive"] = rows
    print()

    # ---- verdict ------------------------------------------------------------
    print("=" * 78)
    print("  VERDICT")
    print("=" * 78)
    tier, why, supported = decide(chromium, len(downward), len(hits),
                                  renderer_best, suspects, rows, fg)
    why = why + suspect_note(suspects)
    report["tier"] = tier
    report["rationale"] = why
    report["supported"] = supported
    print("  TIER %s -- %s" % (tier, "SUPPORTED" if supported else "NOT SUPPORTED"))
    for line in why:
        print("    - %s" % line)
    print("=" * 78)
    return report


def decide(chromium, downward_n, hit_n, renderer_n, suspects, rows, focused):
    why = []


    if chromium and renderer_n > downward_n + 5:
        why.append("the UI is rendered by a browser engine (%d renderer child window(s))"
                   % len(chromium))
        why.append("the app window exposes only %d elements while the renderer child "
                   "exposes %d -- replay searches downward from the app window and "
                   "cannot reach the content" % (downward_n, renderer_n))
        why.append("this is the Electron/CEF/WebView2 class, out of scope for a "
                   "UIA-based tool")
        return "4", why, False

    if chromium:
        why.append("browser-engine child windows are present (%d) -- treat as web-hosted UI"
                   % len(chromium))
        return "4", why, False

    # Only now does focus matter. Renderer-child detection above is a pure Win32
    # fact independent of focus; everything below reads the UIA tree, and several
    # frameworks publish nothing while inactive (TeamViewer drops to 2 elements),
    # which would read as a false Tier 3/4. Refuse to guess.
    if not focused and downward_n <= 3:
        why.append("the window could not be brought to the foreground and exposes "
                   "only %d elements" % downward_n)
        why.append("several frameworks publish nothing while inactive -- click the "
                   "window yourself and re-run before trusting any verdict")
        return "INCONCLUSIVE", why, False

    if downward_n <= 3 and hit_n > downward_n:
        why.append("the app window publishes almost nothing downward (%d elements) "
                   "yet hit-test finds %d -- recording will succeed and replay will not"
                   % (downward_n, hit_n))
        return "4", why, False

    unusable = [d for d in rows if not (d["addressable"] and d["actionable"])]
    if rows and len(unusable) > len(rows) * 0.4:
        why.append("%d of %d interactive controls lack a usable identifier or any "
                   "actionable pattern" % (len(unusable), len(rows)))
        why.append("a custom framework is publishing an incomplete UIA surface -- "
                   "may need a per-family pattern fallback")
        return "2 (needs work)", why, True

    if unusable:
        why.append("%d of %d interactive controls need a pattern fallback "
                   "(no Invoke/Toggle/Select) -- normally solved with "
                   "LegacyIAccessible" % (len(unusable), len(rows)))
        return "2", why, True

    why.append("%d interactive controls, all addressable and all actionable" % len(rows))
    why.append("standard UIA surface -- this is the supported case")
    return "1", why, True


def suspect_note(suspects):
    """Owner-drawn cannot be decided mechanically -- a control with no items may
    simply be empty right now. Report it as a caveat on top of the tier, never
    as the tier itself (7-Zip was misfiled as Tier 3 that way)."""
    if not suspects:
        return []
    return ["CAVEAT: %d sizeable container(s) expose no items: %s"
            % (len(suspects),
               ", ".join("%s %s%s" % (d["controlType"], d.get("dims", "?"),
                                      (" '%s'" % d["name"]) if d["name"] else "")
                         for d in suspects[:4])),
            "  if any of them visibly contains rows, it is owner-drawn and those "
            "rows are not clickable -- keep them out of the recorded flow"]


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--hwnd", type=int)
    g.add_argument("--pid", type=int)
    g.add_argument("--title", help="substring of the window title")
    g.add_argument("--exe", help="path to launch, or just the image name")
    ap.add_argument("--json", help="write the raw findings to this file")
    ap.add_argument("--wait", type=int, default=20,
                    help="seconds to wait for a window after --exe launch")
    args = ap.parse_args()

    target = None
    if args.hwnd:
        wins = [w for w in all_windows() if w["hwnd"] == args.hwnd]
        target = wins[0] if wins else None
    elif args.pid:
        wins = [w for w in all_windows() if w["pid"] == args.pid and w["title"]]
        target = max(wins, key=lambda w: w["area"]) if wins else None
    elif args.title:
        q = args.title.lower()
        pool = all_windows()
        # Prefer exact, then prefix, then substring. A bare substring match picks
        # up terminals and editors whose *tab title* mentions the app name --
        # measured: `--title "TeamViewer"` matched a Windows Terminal window and
        # silently probed the wrong process.
        for pick in (lambda w: w["title"].lower() == q,
                     lambda w: w["title"].lower().startswith(q),
                     lambda w: q in w["title"].lower()):
            wins = [w for w in pool if pick(w)]
            if wins:
                break
        if len(wins) > 1:
            print("[warn] %d windows matched %r; probing the largest. Others:"
                  % (len(wins), args.title))
            for w in sorted(wins, key=lambda w: -w["area"])[1:6]:
                print("       pid=%-7d hwnd=%-9d class=%-22r title=%r"
                      % (w["pid"], w["hwnd"], w["class"][:22], w["title"][:48]))
            print("       (use --hwnd or --pid to be exact)")
        target = max(wins, key=lambda w: w["area"]) if wins else None
    else:
        image = args.exe.split("\\")[-1]

        def visible_wins():
            pids = pids_for_image(image)
            return [w for w in all_windows() if w["pid"] in pids and w["title"]]

        wins = visible_wins()
        # A running process with no visible window still needs a launch: many
        # apps sit in the tray, and single-instance apps re-show their existing
        # window when launched again rather than creating a new process.
        if not wins:
            print("[launch] %s" % args.exe)
            try:
                subprocess.Popen([args.exe], close_fds=True)
            except Exception as e:
                print("launch failed: %s" % e)
                return 1
            for _ in range(args.wait):
                time.sleep(1)
                wins = visible_wins()
                if wins:
                    time.sleep(4)          # let the UI finish painting
                    wins = visible_wins()
                    break
        target = max(wins, key=lambda w: w["area"]) if wins else None

    if not target:
        print("no visible window matched. Visible windows:")
        for w in sorted(all_windows(), key=lambda w: -w["area"])[:25]:
            if w["title"]:
                print("  pid=%-7d hwnd=%-9d class=%-24r title=%r"
                      % (w["pid"], w["hwnd"], w["class"][:24], w["title"][:50]))
        return 1

    report = probe(target["hwnd"], target)

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2, default=str)
        print("\nraw findings written to %s" % args.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
