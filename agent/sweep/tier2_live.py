"""
Tier 2 -- the honest one. Physically clicks a control while agent.py records,
generates from that real capture, replays it with node, and then checks
whether anything actually happened.

WHY THIS EXISTS: Tier 1 synthesizes events, so it flies past the capture layer
entirely -- exactly the blind spot mock_events.py has. The two most recent real
bugs (2026-08-05 dead-element adoption, menu-snapshot race) both lived there.
Only a real click through pynput/UIA can reach them.

THE CENTRAL CHECK is not "did replay report success". `_step()` swallows a
failure when its ESC/popup recovery path succeeds (server.js:669 resets
`_failures.length = before`), and `Legacy.Select()` returns cleanly without
moving a checkbox (CLAUDE.md §3). So a `[PASS]` line proves nothing on its
own. This module records what the control's UIA state looked like before and
after the physical click, and calls it FALSE-PASS when replay claims success
but the observed state never moved.

SAFETY: never runs without --yes. Only touches controls the enumeration marked
safe. Kills and relaunches the app for every control so no click inherits the
previous one's state. Any control that makes the app window disappear is
written to the learned denylist and never clicked again.
"""

import argparse
import ctypes
import os
import re
import subprocess
import sys
import time
import types

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import (  # noqa: E402
    request, get_uia, describe, patterns_of, find_all_settled, all_windows,
    activate, capture_one, top_level_windows_snapshot,
    REPO_ROOT, add_learned_deny, control_key, get_app, read_controls,
    write_report,
)
import enumerate_controls as enum  # noqa: E402

REPLAY_TIMEOUT = 240
# Seconds to let the agent's worker thread discover the freshly launched
# window before clicking into it.
AGENT_SETTLE = 2.0
# Seconds to wait for a captured click to reach the server before concluding
# the capture layer dropped it.
CAPTURE_WAIT = 6.0


def is_elevated():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def kill_app(exe):
    """Hard reset between controls. Only ever targets the app under test."""
    if "!" in exe:  # UWP -- no image name to kill; /api/start's state reset covers it
        return
    image = os.path.basename(exe)
    subprocess.run(["taskkill", "/IM", image, "/F", "/T"],
                   capture_output=True, text=True)
    time.sleep(1.0)


def resolve_control(uia, win, ctrl):
    """Find this control again in the live tree. The cached rect is NOT trusted
    -- the window lands somewhere different every launch, and a stale
    coordinate is how you click the wrong thing."""
    try:
        root = uia.ElementFromHandle(win["hwnd"])
    except Exception:
        return None
    if not root:  # comtypes returns a NULL pointer, not None
        return None
    want = ctrl["key"]
    seen = []
    for el in find_all_settled(uia, root, timeout=6.0, quiet_for=0.8):
        d = describe(el)
        d["patterns"] = patterns_of(el)
        seen.append(control_key(d))
        if control_key(d) != want:
            continue
        try:
            r = el.CurrentBoundingRectangle
        except Exception:
            continue
        if r.right <= r.left or r.bottom <= r.top:
            continue
        return {"element": el, "rect": [r.left, r.top, r.right, r.bottom],
                "centre": [(r.left + r.right) // 2, (r.top + r.bottom) // 2]}
    # Not found -- hand back what WAS there so the failure is diagnosable
    # instead of just "absent".
    name = (ctrl.get("name") or "").strip()
    near = [k for k in seen if name and name in k][:5]
    return {"missing": True, "scanned": len(seen), "near": near,
            "sample": seen[:12]}


def run_replay(app_name):
    """node <App>TestById.js -> structured outcome."""
    folder = os.path.join(REPO_ROOT, "generated-wdio", app_name)
    script = "%sTestById.js" % app_name
    path = os.path.join(folder, script)
    if not os.path.exists(path):
        return {"ran": False, "reason": "generated script missing: %s" % path}
    try:
        p = subprocess.run(["node", script], cwd=folder, capture_output=True,
                           text=True, encoding="utf-8", errors="replace",
                           timeout=REPLAY_TIMEOUT)
    except subprocess.TimeoutExpired:
        return {"ran": True, "timedOut": True, "pass": False,
                "failures": ["replay-timeout>%ds" % REPLAY_TIMEOUT], "steps": []}
    out = (p.stdout or "") + "\n" + (p.stderr or "")
    fails = re.findall(r"\[FAIL\]\s*(\[[^\]]*\])", out)
    fatal = "[FATAL]" in out
    return {
        "ran": True,
        "exitCode": p.returncode,
        "pass": ("[PASS] all steps completed" in out) and p.returncode == 0 and not fatal,
        "fatal": fatal,
        "failures": fails,
        "steps": re.findall(r"\[STEP\]\s*(.+)", out),
        "tail": out.strip()[-1500:],
    }


def one_control(uia, entry, ctrl, keep_open=False):
    """Full record -> generate -> replay -> verify cycle for one control."""
    res = {"control": "%s %r aid=%r cls=%r" % (
        ctrl["controlType"], ctrl.get("name"), ctrl.get("automationId"),
        ctrl.get("className")), "key": ctrl["key"], "verdicts": []}

    kill_app(entry["exePath"])
    status, body = request("POST", "/api/start", {
        "appName": entry["appName"], "exePath": entry["exePath"],
        "platform": entry["platform"]}, timeout=45)
    if status != 200 or not body.get("ok"):
        res["verdicts"].append("START-FAILED")
        res["detail"] = "status=%s %s" % (status, str(body)[:200])
        return res

    try:
        win, _ = enum.launch_and_wait(entry, timeout=25)
    except SystemExit as e:
        request("POST", "/api/stop", {})
        res["verdicts"].append("WINDOW-NOT-FOUND")
        res["detail"] = str(e)
        return res
    activate(win["hwnd"])
    # The agent's worker thread has to finish discovering the target window
    # before a click on it means anything. Clicking the instant the window is
    # visible produced NOT-CAPTURED on the first live run (2026-08-05).
    time.sleep(AGENT_SETTLE)

    live = resolve_control(uia, win, ctrl)
    if isinstance(live, dict) and live.get("missing") and live.get("scanned", 0) <= 2:
        # A window whose whole subtree is the Window + its TitleBar is not an
        # app without accessibility -- it is a UIPI barrier. agent.py runs
        # elevated, so the app IT launches is elevated too, and a non-elevated
        # reader gets a stub tree. Measured 2026-08-05: 7-Zip enumerated 243
        # elements when the user launched it, 2 when the agent did.
        request("POST", "/api/stop", {})
        res["verdicts"].append("UIA-BLOCKED-BY-ELEVATION")
        res["detail"] = (
            "the app window exposed only %d UIA elements. agent.py launched it "
            "elevated; this sweep process is not, so UIPI hides the tree. Run "
            "the sweep from an ADMINISTRATOR PowerShell, the same one agent.py "
            "needs (CLAUDE.md §5)." % live.get("scanned", 0))
        res["scanned"] = live.get("scanned")
        return res
    if not live or live.get("missing"):
        request("POST", "/api/stop", {})
        res["verdicts"].append("CONTROL-NOT-FOUND-AT-REPLAY")
        res["detail"] = ("enumerated once but absent from the freshly launched "
                         "tree -- state-dependent control, or the enumeration "
                         "was taken in a different app state")
        res["window"] = {"hwnd": win["hwnd"], "title": win["title"],
                         "class": win["class"]}
        res["wantKey"] = ctrl["key"]
        if isinstance(live, dict):
            res["scanned"] = live.get("scanned")
            res["nearMatches"] = live.get("near")
            res["sampleKeys"] = live.get("sample")
        return res

    x, y = live["centre"]
    res["clickPoint"] = [x, y]
    wins_before = top_level_windows_snapshot()
    observed = capture_one(uia, x, y, types.SimpleNamespace(live=True, double=False))
    res["observed"] = observed["verdicts"]
    wins_after = top_level_windows_snapshot()

    app_gone = win["hwnd"] not in wins_after
    if app_gone:
        res["verdicts"].append("APP-WINDOW-CLOSED")
        add_learned_deny(entry["app"], ctrl["key"],
                         "clicking it closed the app window")

    # agent.py's pynput callback only enqueues; the UIA inspection happens on
    # the worker thread afterwards -- measured 0.04-0.09s for a plain click but
    # 0.9-1.0s for a menu item (2026-08-05). Reading /api/events immediately
    # after the click reports zero events for a click that was captured fine,
    # so poll for the event to land before calling it NOT-CAPTURED.
    captured = []
    deadline = time.time() + CAPTURE_WAIT
    while time.time() < deadline:
        _, events = request("GET", "/api/events")
        captured = [e for e in (events or []) if isinstance(e, dict)
                    and e.get("action") != "session_meta"]
        if captured:
            break
        time.sleep(0.25)
    request("POST", "/api/stop", {})
    res["capturedEvents"] = len(captured)
    if not captured:
        res["verdicts"].append("NOT-CAPTURED")
        res["detail"] = ("the physical click produced no event -- this is a "
                         "capture-layer (agent.py) gap, invisible to Tier 1")
        return res

    status, body = request("POST", "/api/generate", {
        "appName": entry["appName"], "exePath": entry["exePath"],
        "platform": entry["platform"]}, timeout=60)
    if status != 200 or not body.get("ok"):
        res["verdicts"].append("GENERATE-FAILED")
        res["detail"] = "status=%s %s" % (status, str(body)[:200])
        return res

    if not keep_open:
        kill_app(entry["exePath"])
    replay = run_replay(entry["appName"])
    res["replay"] = {k: v for k, v in replay.items() if k != "tail"}
    res["replayTail"] = replay.get("tail", "")

    state_moved = any(v in ("FALSE-SUCCESS-RISK",) for v in observed["verdicts"])
    # capture_one reports FALSE-SUCCESS-RISK when a TogglePattern did NOT move.
    # Absence of that verdict plus a new/closed window or a dead element is the
    # positive evidence that something happened.
    evidence = [v for v in observed["verdicts"]
                if v in ("CROSS-WINDOW", "WINDOW-CLOSED", "SELF-DESTROYING")]
    if replay.get("pass") and not evidence and state_moved:
        res["verdicts"].append("FALSE-PASS")
        res["detail"] = ("replay reported [PASS] but the physical click never "
                         "moved this control's ToggleState either -- nothing "
                         "proves the action reached the app (CLAUDE.md §3)")
    elif replay.get("ran") and not replay.get("pass"):
        res["verdicts"].append("REPLAY-FAILED")
    elif replay.get("pass"):
        res["verdicts"].append("OK")
    return res


def run(app, max_controls=3, confirm=False, name_filter=None):
    entry = get_app(app)
    cache = read_controls(entry["app"])
    targets = [c for c in cache["controls"]
               if c["clickable"] and c["safety"] == "safe" and not c["flags"]]
    if name_filter:
        rx = re.compile(name_filter)
        targets = [c for c in targets if rx.search(c.get("name") or "")]
    targets = targets[:max_controls]

    print("app     : %s (%s)" % (entry["app"], entry["exePath"]))
    print("planned : %d control(s) -- each gets its own app restart" % len(targets))
    for c in targets:
        print("   %-12s %r" % (c["controlType"], c.get("name")))
    if not confirm:
        print("\nDRY RUN. Nothing was clicked. Re-run with --yes to execute.")
        return {"dryRun": True, "planned": [c["key"] for c in targets]}

    if not is_elevated():
        raise SystemExit(
            "sweep: this process is NOT elevated.\n"
            "  agent.py runs as Administrator, so every app it launches is "
            "elevated too, and a non-elevated reader sees a 2-element stub "
            "tree instead of the real one (UIPI). Tier 2 would report every "
            "control as missing.\n"
            "  Re-run this from an ADMINISTRATOR PowerShell.")

    ok, st = request("GET", "/api/status")
    if not st.get("agentOnline"):
        raise SystemExit("sweep: agent is offline -- tier 2 records through it. "
                         "Start it in an ADMIN PowerShell: cd agent; python agent.py")
    if not st.get("isAdmin"):
        raise SystemExit("sweep: agent is not elevated -- automationId/name come "
                         "back empty (CLAUDE.md §5). Restart it as Administrator.")

    uia, _ = get_uia()
    results = []
    for i, c in enumerate(targets, 1):
        print("\n[%d/%d] %s %r" % (i, len(targets), c["controlType"], c.get("name")))
        r = one_control(uia, entry, c)
        print("      -> %s" % (", ".join(r["verdicts"]) or "?"))
        results.append(r)

    payload = {"app": entry["app"], "appName": entry["appName"],
               "tested": len(results), "results": results}
    path = write_report(entry["app"], "live", payload)
    print("\nreport  : %s" % path)
    for r in results:
        print("   %-40s %s" % (r["control"][:40], ", ".join(r["verdicts"])))
    return payload


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--app", required=True)
    ap.add_argument("--max-controls", type=int, default=3)
    ap.add_argument("--name", default=None, help="regex; only controls whose name matches")
    ap.add_argument("--yes", action="store_true",
                    help="actually click. Without this the run is a dry listing.")
    args = ap.parse_args()
    run(args.app, args.max_controls, args.yes, args.name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
