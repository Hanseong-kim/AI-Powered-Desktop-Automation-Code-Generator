"""PoC — why TeamViewer 15 cannot be automated by a UIA-based record/replay tool.

This script is written to be run by a third party and to produce a report that
can be pasted into an email. It does not depend on this project's code; it
talks to Windows UI Automation directly (the same API Microsoft's inspect.exe
uses). Read-only unless --actuate is passed.

    pip install comtypes
    python PoC_teamviewer.py                 # measure only (safe, read-only)
    python PoC_teamviewer.py --launch        # start TeamViewer first if needed
    python PoC_teamviewer.py --actuate       # additionally try to CLICK a toggle
    python PoC_teamviewer.py --json out.json # machine-readable copy of results

--------------------------------------------------------------------------
WHY inspect.exe SHOWING THE ELEMENTS IS NOT A COUNTER-ARGUMENT
--------------------------------------------------------------------------
inspect.exe locates elements by hit-testing the pixel under the mouse
(IUIAutomation::ElementFromPoint). So does a recorder: it knows where the user
clicked. That path works on TeamViewer, which is exactly why recording produces
plausible-looking XPaths.

Replay cannot use that path. There is no mouse position to hit-test at replay
time — the whole point is to find the element from its selector. Every UIA
driver (WinAppDriver included) therefore searches DOWNWARD from the application
window with FindAll/FindFirst. This script measures both directions, plus
whether the elements can be actuated at all once found.

TEST 1  reachability      downward search from the app window vs. hit-test
TEST 2  selector quality  are AutomationId / ClassName usable as selectors?
TEST 3  actuation         does clicking/toggling change anything? (--actuate)
"""
import argparse
import ctypes
import json
import subprocess
import sys
import time
from ctypes import wintypes

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

try:
    import comtypes
    import comtypes.client
except ImportError:
    sys.exit("comtypes is required:  pip install comtypes")

EXE = r"C:\Program Files\TeamViewer\TeamViewer.exe"
PROC_NAME = "teamviewer.exe"

# --- UIA constants ---------------------------------------------------------
UIA_ControlTypeProperty = 30003
TreeScope_Subtree = 7
UIA_TogglePatternId = 10015
UIA_InvokePatternId = 10000
UIA_LegacyIAccessiblePatternId = 10018
CT_CHECKBOX = 50002
CT_RADIO = 50013
CT_BUTTON = 50000

user32 = ctypes.windll.user32


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


# --- win32 helpers ---------------------------------------------------------
def window_text(hwnd):
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def class_name(hwnd):
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def pid_of(hwnd):
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def rect_of(hwnd):
    r = RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return (r.left, r.top, r.right, r.bottom)


ENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)


def top_level_windows():
    out = []
    def cb(hwnd, _):
        out.append(hwnd)
        return True
    user32.EnumWindows(ENUMPROC(cb), 0)
    return out


def child_windows(parent):
    """All descendants of `parent`, breadth-first."""
    found, frontier = [], [parent]
    while frontier:
        cur = frontier.pop(0)
        kids = []
        def cb(hwnd, _):
            kids.append(hwnd)
            return True
        user32.EnumChildWindows(cur, ENUMPROC(cb), 0)
        for k in kids:
            if k not in found:
                found.append(k)
                frontier.append(k)
    return found


def teamviewer_pids():
    try:
        out = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {PROC_NAME}", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=15).stdout
    except Exception:
        return set()
    pids = set()
    for line in out.splitlines():
        parts = [p.strip('"') for p in line.split('","')]
        if len(parts) >= 2 and parts[0].lower() == PROC_NAME:
            try:
                pids.add(int(parts[1]))
            except ValueError:
                pass
    return pids


def find_main_window():
    """Largest visible top-level window belonging to TeamViewer.exe."""
    pids = teamviewer_pids()
    if not pids:
        return None
    best, best_area = None, 0
    for hwnd in top_level_windows():
        if pid_of(hwnd) not in pids or not user32.IsWindowVisible(hwnd):
            continue
        l, t, r, b = rect_of(hwnd)
        area = max(0, r - l) * max(0, b - t)
        if area > best_area:
            best, best_area = hwnd, area
    return best if best_area > 10000 else None


# --- UIA helpers -----------------------------------------------------------
class Uia:
    def __init__(self):
        comtypes.CoInitialize()
        self.mod = comtypes.client.GetModule("UIAutomationCore.dll")
        self.api = comtypes.client.CreateObject(
            "{ff48dba4-60ef-4201-aa87-54103eef594e}",
            interface=self.mod.IUIAutomation)
        self.last_point_error = None

    def from_handle(self, hwnd):
        try:
            el = self.api.ElementFromHandle(hwnd)
            # comtypes returns a NULL pointer (not None) on a miss — test
            # truthiness, never `is not None`.
            return el if el else None
        except Exception:
            return None

    def from_point(self, x, y):
        # wintypes.POINT — a locally declared ctypes.Structure with the same
        # layout is NOT accepted by the comtypes-generated signature and makes
        # every call raise, which silently reads as "0 hits" (own bug, hit
        # while writing this script).
        try:
            el = self.api.ElementFromPoint(wintypes.POINT(int(x), int(y)))
            return el if el else None
        except Exception as e:
            self.last_point_error = f"{type(e).__name__}: {e}"
            return None

    def subtree_count(self, root):
        """How many elements a downward search finds — the replay path."""
        try:
            found = root.FindAll(TreeScope_Subtree, self.api.CreateTrueCondition())
            return found.Length if found else 0
        except Exception:
            return 0

    def subtree(self, root):
        try:
            found = root.FindAll(TreeScope_Subtree, self.api.CreateTrueCondition())
            return [found.GetElement(i) for i in range(found.Length)] if found else []
        except Exception:
            return []

    @staticmethod
    def describe(el):
        def get(fn, default=""):
            try:
                return fn() or default
            except Exception:
                return default
        return {
            "name": get(lambda: el.CurrentName),
            "automationId": get(lambda: el.CurrentAutomationId),
            "className": get(lambda: el.CurrentClassName),
            "controlType": get(lambda: el.CurrentControlType, 0),
        }

    def toggle_state(self, el):
        try:
            p = el.GetCurrentPattern(UIA_TogglePatternId)
            if not p:
                return None
            return p.QueryInterface(
                self.mod.IUIAutomationTogglePattern).CurrentToggleState
        except Exception:
            return None


def hr(title):
    print()
    print("=" * 74)
    print(title)
    print("=" * 74)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--launch", action="store_true",
                    help="start TeamViewer if no window is showing")
    ap.add_argument("--actuate", action="store_true",
                    help="TEST 3: actually try to toggle a checkbox (modifies UI state)")
    ap.add_argument("--json", metavar="PATH", help="write results as JSON")
    args = ap.parse_args()

    report = {"tool": "PoC_teamviewer", "timestamp": time.time()}

    hwnd = find_main_window()
    if hwnd is None and args.launch:
        print("[*] no TeamViewer window found — launching ...")
        try:
            subprocess.Popen([EXE])
        except Exception as e:
            sys.exit(f"could not launch {EXE}: {e}")
        for _ in range(40):
            time.sleep(0.5)
            hwnd = find_main_window()
            if hwnd:
                break
    if hwnd is None:
        sys.exit("No visible TeamViewer window. Start TeamViewer (or pass --launch) "
                 "and run again. NOTE: the TeamViewer_Service process alone is not "
                 "enough - the GUI window must be open.")

    uia = Uia()
    app_rect = rect_of(hwnd)
    print(f"[*] TeamViewer main window hwnd={hwnd} class={class_name(hwnd)!r} "
          f"title={window_text(hwnd)!r}")
    print(f"[*] rect={app_rect}")
    report["window"] = {"hwnd": hwnd, "class": class_name(hwnd),
                        "title": window_text(hwnd), "rect": app_rect}

    # ---------------------------------------------------------------- TEST 1
    hr("TEST 1 — Reachability: can replay find the UI at all?")

    # The app-window subtree is NOT stable right after the window appears —
    # it grows while the UI settles. Sampling once produces a number that says
    # more about timing than about the app, so sample repeatedly and report
    # the whole series.
    app_el = uia.from_handle(hwnd)
    settle = []
    for delay in (0.0, 1.0, 2.0, 4.0):
        if delay:
            time.sleep(delay)
        settle.append(uia.subtree_count(app_el) if app_el else 0)
    app_count = max(settle)
    print(f"downward search from the APP WINDOW           : {app_count:>5} elements")
    print(f"   sampled at t=0s,1s,3s,7s -> {settle}")
    print("   (this is the path WinAppDriver and any replay engine uses)")

    kids = child_windows(hwnd)
    renderers = [k for k in kids if class_name(k).startswith("Chrome_WidgetWin")]
    renderer_rows = []
    best_renderer, best_count = None, 0
    for k in renderers:
        el = uia.from_handle(k)
        c = uia.subtree_count(el) if el else 0
        renderer_rows.append({"hwnd": k, "class": class_name(k), "count": c})
        print(f"downward search from {class_name(k):<22} hwnd={k:<9}: {c:>5} elements")
        if c > best_count:
            best_renderer, best_count = k, c

    # Hit-test sampling: the RECORDER path. Sample a grid inside the window.
    l, t, r, b = app_rect
    hits, named = 0, 0
    seen = set()
    for gx in range(1, 10):
        for gy in range(1, 10):
            x = l + (r - l) * gx // 10
            y = t + (b - t) * gy // 10
            el = uia.from_point(x, y)
            if not el:
                continue
            hits += 1
            d = uia.describe(el)
            key = (d["name"], d["automationId"], d["className"], d["controlType"])
            seen.add(key)
            if d["name"]:
                named += 1
    print(f"hit-test (ElementFromPoint) over an 9x9 grid  : {hits:>5} hits, "
          f"{len(seen)} distinct elements, {named} with a Name")
    print("   (this is the path the RECORDER — and inspect.exe — uses)")
    if hits == 0 and uia.last_point_error:
        print(f"   !! every hit-test call FAILED: {uia.last_point_error}")
        print("   !! treat the hit-test column as NO DATA, not as a finding.")

    report["test1"] = {"app_window_settle": settle,
                       "app_window_subtree": app_count,
                       "renderers": renderer_rows,
                       "hit_test_hits": hits,
                       "hit_test_distinct": len(seen),
                       "hit_test_named": named}

    asymmetric = best_count > app_count * 3 and best_count > 10
    print()
    if asymmetric:
        print(f"==> ASYMMETRY CONFIRMED: {app_count} elements downward from the app "
              f"window vs {best_count} from the")
        print(f"    embedded browser child window. The UI is real, but it is NOT in "
              f"the app window's")
        print(f"    subtree, so a selector-based replay searching from the app window "
              f"finds nothing.")
    else:
        print("==> No asymmetry measured on this build — the app window subtree "
              "looks complete.")

    # ---------------------------------------------------------------- TEST 2
    hr("TEST 2 — Selector quality: are AutomationId / ClassName usable?")

    search_root = uia.from_handle(best_renderer) if best_renderer else app_el
    elements = uia.subtree(search_root) if search_root else []
    interactive = []
    for el in elements:
        d = uia.describe(el)
        if d["controlType"] in (CT_CHECKBOX, CT_RADIO, CT_BUTTON):
            interactive.append((el, d))

    no_id = sum(1 for _, d in interactive if not d["automationId"])
    no_cls = sum(1 for _, d in interactive if not d["className"])
    print(f"interactive controls found (Button/CheckBox/Radio): {len(interactive)}")
    print(f"   with EMPTY AutomationId : {no_id}/{len(interactive)}")
    print(f"   with EMPTY ClassName    : {no_cls}/{len(interactive)}")
    print()
    print("sample (first 10):")
    for _, d in interactive[:10]:
        print(f"   name={d['name'][:34]!r:<36} automationId={d['automationId']!r:<16} "
              f"className={d['className']!r}")
    report["test2"] = {"interactive": len(interactive),
                       "empty_automation_id": no_id,
                       "empty_class_name": no_cls,
                       "sample": [d for _, d in interactive[:10]]}
    print()
    print("==> An AutomationId that is empty cannot be a selector. An AutomationId "
          "that looks like")
    print("    'TextField113' / 'field-542' is a render counter from the web "
          "framework — it changes")
    print("    between builds, so it cannot be a STABLE selector either. Re-run "
          "after a TeamViewer")
    print("    update to see the ids move.")

    # ---------------------------------------------------------------- TEST 3
    hr("TEST 3 — Actuation: once found, can the element be driven?")

    toggles = [(el, d) for el, d in interactive
               if d["controlType"] in (CT_CHECKBOX, CT_RADIO)]
    if not args.actuate:
        print(f"{len(toggles)} toggleable control(s) found. SKIPPED — pass --actuate "
              "to run this test.")
        print("   (--actuate attempts a real toggle and therefore changes UI state.)")
        report["test3"] = {"skipped": True, "toggles_found": len(toggles)}
    elif not toggles:
        print("No CheckBox/RadioButton found to actuate.")
        report["test3"] = {"skipped": False, "toggles_found": 0}
    else:
        results = []
        for el, d in toggles[:5]:
            before = uia.toggle_state(el)
            attempts = {}
            # (a) TogglePattern.Toggle()
            try:
                p = el.GetCurrentPattern(UIA_TogglePatternId)
                if p:
                    p.QueryInterface(uia.mod.IUIAutomationTogglePattern).Toggle()
                    attempts["TogglePattern.Toggle"] = "returned without error"
                else:
                    attempts["TogglePattern.Toggle"] = "pattern not supported"
            except Exception as e:
                attempts["TogglePattern.Toggle"] = f"raised {type(e).__name__}"
            time.sleep(0.3)
            after_toggle = uia.toggle_state(el)
            # (b) InvokePattern
            try:
                p = el.GetCurrentPattern(UIA_InvokePatternId)
                if p:
                    p.QueryInterface(uia.mod.IUIAutomationInvokePattern).Invoke()
                    attempts["InvokePattern.Invoke"] = "returned without error"
                else:
                    attempts["InvokePattern.Invoke"] = "pattern not supported"
            except Exception as e:
                attempts["InvokePattern.Invoke"] = f"raised {type(e).__name__}"
            time.sleep(0.3)
            after_invoke = uia.toggle_state(el)

            changed = len({s for s in (before, after_toggle, after_invoke)
                           if s is not None}) > 1
            print(f"   {d['name'][:40]!r}")
            for k, v in attempts.items():
                print(f"      {k:<24} -> {v}")
            print(f"      ToggleState  before={before}  after Toggle()={after_toggle}"
                  f"  after Invoke()={after_invoke}   CHANGED={changed}")
            results.append({"name": d["name"], "before": before,
                            "after_toggle": after_toggle,
                            "after_invoke": after_invoke,
                            "changed": changed, "attempts": attempts})
        any_changed = any(r["changed"] for r in results)
        report["test3"] = {"skipped": False, "results": results,
                           "any_state_changed": any_changed}
        print()
        if any_changed:
            print("==> At least one control DID change state — actuation works here.")
        else:
            print("==> No control changed state. The accessibility layer reports the "
                  "control, but the")
            print("    reported state is not wired to the application's real state. "
                  "There is nothing an")
            print("    external UIA client can do about this.")

    # ---------------------------------------------------------------- verdict
    hr("VERDICT")
    print("A record/replay tool built on UI Automation needs three things.")
    print("Each line below reports what THIS run measured — nothing is assumed.")
    print()

    find_v = "BLOCKED" if asymmetric else f"reachable ({app_count} elements)"
    print(f"  1. find the element by searching down from the window : {find_v}")

    if not interactive:
        sel_v = "no interactive controls found"
    elif no_id == len(interactive):
        sel_v = "BLOCKED (no AutomationId at all)"
    else:
        sel_v = (f"WEAK ({no_id}/{len(interactive)} have no AutomationId; "
                 f"see the ClassName values)")
    print(f"  2. a stable AutomationId/ClassName for the selector    : {sel_v}")

    if not args.actuate:
        act_v = "not tested (pass --actuate)"
    else:
        rs = report["test3"].get("results", [])
        ok = sum(1 for r in rs if r["changed"])
        act_v = f"{ok}/{len(rs)} controls actually changed state" if rs else "no toggles found"
    print(f"  3. actuate it                                          : {act_v}")

    print()
    print("How to read this:")
    print("  * Recording only needs the hit-test path, which is also what "
          "inspect.exe uses.")
    print("    inspect.exe showing an element therefore says nothing about "
          "whether replay works.")
    print("  * Replay needs all three lines above to hold. A single BLOCKED line "
          "is enough to")
    print("    stop it; WEAK on line 2 means selectors survive until the next "
          "build of the app.")
    print("  * Line 1 is timing-sensitive: see the settle series in TEST 1. "
          "Measuring once,")
    print("    immediately after launch, understates it.")

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        print(f"\n[*] wrote {args.json}")


if __name__ == "__main__":
    main()
