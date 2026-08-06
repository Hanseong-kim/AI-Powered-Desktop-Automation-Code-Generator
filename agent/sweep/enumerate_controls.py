"""
Tier 0 -- enumerate every control in an app's window, with no human hovering
a mouse over anything.

This replaces the manual step that `poc/probe_click_replay.py` still requires
(hover, press ENTER, repeat). It walks the UIA tree downward from the target
window -- the same direction REPLAY searches -- rather than hit-testing a
pixel like the recorder does. That difference is deliberate and is the whole
reason this is worth doing: an element inspect.exe finds under the cursor may
still be unreachable from a downward search, and only a downward walk can tell
you that before you spend a recording on it (CLAUDE.md §4, "On inspect.exe").

Output is cached to controls/<App>.json so the Tier 1 fix loop can iterate in
seconds against the server alone, without relaunching the app -- the same
reason mock_events.py runs without a GUI.
"""

import argparse
import ctypes
import os
import subprocess
import sys
import time
from ctypes import wintypes

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import (  # noqa: E402
    get_uia, describe, patterns_of, activate, all_windows, pids_for_image,
    find_all_settled, ACTIONABLE_PATTERNS, INTERACTIVE, classify_safety,
    control_key, deny_regex, get_app, load_learned_deny, write_controls,
)

u = ctypes.windll.user32


def launch_and_wait(entry, timeout=25):
    """Start the app (or adopt an already-running one) and return its window.

    Returns (window_dict, launched_by_us). Uses the same 'not in the pre-launch
    baseline' rule launchApp() uses in generated code, so a single-instance app
    that refuses a second launch is adopted instead of timing out (CLAUDE.md §5
    'Single-instance apps break launchApp()').
    """
    exe = entry["exePath"]
    hint = (entry.get("titleHint") or entry["app"]).lower()

    before = {w["hwnd"] for w in all_windows()}
    existing = _match_windows(hint, exe)
    if existing:
        w = max(existing, key=lambda w: w["area"])
        activate(w["hwnd"])
        return w, False

    if "!" in exe:  # UWP AUMID
        subprocess.Popen(["explorer.exe", "shell:AppsFolder\\%s" % exe])
    else:
        subprocess.Popen([exe], cwd=os.path.dirname(exe) or None)

    deadline = time.time() + timeout
    while time.time() < deadline:
        cands = [w for w in _match_windows(hint, exe) if w["hwnd"] not in before]
        if cands:
            w = max(cands, key=lambda w: w["area"])
            activate(w["hwnd"])
            return w, True
        time.sleep(0.4)
    raise SystemExit("sweep: %s window did not appear within %ds (exe=%s)"
                     % (entry["app"], timeout, exe))


def _match_windows(hint, exe):
    image = os.path.basename(exe) if "!" not in exe else ""
    pids = pids_for_image(image) if image else set()
    out = []
    for w in all_windows():
        if not w["title"] or w["area"] < 10000:
            continue
        if (pids and w["pid"] in pids) or hint in w["title"].lower():
            out.append(w)
    return out


def enumerate_window(uia, win):
    """Every element in the window's subtree, with the facts a selector needs."""
    try:
        root = uia.ElementFromHandle(win["hwnd"])
    except Exception as e:
        raise SystemExit("sweep: ElementFromHandle(%d) failed: %s" % (win["hwnd"], e))
    if not root:  # comtypes returns a NULL pointer, not None (CLAUDE.md §5)
        raise SystemExit("sweep: no UIA element for hwnd %d" % win["hwnd"])

    els = find_all_settled(uia, root)
    out = []
    for el in els:
        d = describe(el)
        try:
            r = el.CurrentBoundingRectangle
            rect = [r.left, r.top, r.right, r.bottom]
        except Exception:
            rect = None
        try:
            off = bool(el.CurrentIsOffscreen)
        except Exception:
            off = False
        d["rect"] = rect
        d["offscreen"] = off
        d["patterns"] = patterns_of(el)
        out.append(d)
    return out


def annotate(controls, win, entry):
    """Add the judgements the later tiers act on. Nothing is dropped here --
    a control with no usable selector is a FINDING, not noise, and must stay
    in the report (CLAUDE.md §3 'no false PASS')."""
    deny_re = deny_regex(entry)
    learned = load_learned_deny().get(entry["app"], {})
    wl, wt, wr, wb = win["rect"]

    seen_ids = {}
    for c in controls:
        aid = c.get("automationId") or ""
        if aid:
            seen_ids[aid] = seen_ids.get(aid, 0) + 1

    annotated = []
    for c in controls:
        rect = c.get("rect")
        visible = bool(rect) and rect[2] > rect[0] and rect[3] > rect[1] and not c["offscreen"]
        inside = bool(rect) and rect[0] >= wl - 2 and rect[1] >= wt - 2 and \
            rect[2] <= wr + 2 and rect[3] <= wb + 2
        # LegacyIAccessible is published by nearly everything -- 189 of 243
        # elements in a 7-Zip window (measured 2026-08-05), including all 154
        # static date/size Text cells. Treating it as proof of actionability
        # made every label a click target. A real pattern counts on its own;
        # Legacy only counts when the ControlType is one a user actually
        # clicks (INTERACTIVE, from probe_app_automatability.py). That keeps
        # the 7-Zip breadcrumb Edits -- which genuinely expose Legacy only --
        # while dropping the Text cells.
        strong = [p for p in c["patterns"]
                  if p in ACTIONABLE_PATTERNS and p != "Legacy"]
        legacy_only = "Legacy" in c["patterns"] and not strong
        actionable = bool(strong) or (legacy_only and c["ctId"] in INTERACTIVE)
        addressable = bool(c.get("automationId") or c.get("name") or c.get("className"))

        flags = []
        if not addressable:
            flags.append("NO-SELECTOR")
        if not actionable:
            flags.append("NO-ACTIONABLE-PATTERN")
        if not visible:
            flags.append("NOT-VISIBLE")
        if rect and not inside:
            flags.append("OUTSIDE-WINDOW")
        aid = c.get("automationId") or ""
        if aid and seen_ids.get(aid, 0) > 1:
            # PuTTY reuses resource ids across panels -- codegen must AND the
            # name in, or replay clicks whichever one it finds first.
            flags.append("DUPLICATE-AUTOMATIONID")
        if aid and _looks_like_hwnd(aid):
            # VCL (HeidiSQL) fills AutomationId with the window handle, which
            # changes every launch (CLAUDE.md §2 Tier 2).
            flags.append("HWND-AS-AUTOMATIONID")

        c["flags"] = flags
        c["key"] = control_key(c)
        safety, reason = classify_safety(c, deny_re, learned)
        c["safety"] = safety
        c["safetyReason"] = reason
        c["clickable"] = actionable and visible and inside and safety != "skip"
        # centre point, recomputed at click time in tier 2 -- stored only for
        # diagnostics. Generated code never sees a coordinate (CLAUDE.md §3).
        c["centre"] = [(rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2] if rect else None
        annotated.append(c)
    return annotated


def _looks_like_hwnd(aid):
    if not aid.isdigit():
        return False
    n = int(aid)
    return n > 65535 and u.IsWindow(wintypes.HWND(n))


def run(app, timeout=25):
    entry = get_app(app)
    win, launched = launch_and_wait(entry, timeout)
    uia, _ = get_uia()
    raw = enumerate_window(uia, win)
    controls = annotate(raw, win, entry)

    payload = {
        "app": entry["app"],
        "appName": entry["appName"],
        "exePath": entry["exePath"],
        "platform": entry["platform"],
        "window": {"hwnd": win["hwnd"], "title": win["title"],
                   "class": win["class"], "rect": list(win["rect"])},
        "launchedByHarness": launched,
        "total": len(controls),
        "clickable": sum(1 for c in controls if c["clickable"]),
        "safe": sum(1 for c in controls if c["clickable"] and c["safety"] == "safe"),
        "noSelector": sum(1 for c in controls if "NO-SELECTOR" in c["flags"]),
        "controls": controls,
    }
    path = write_controls(entry["app"], payload)

    print("app          : %s  (window %r, hwnd=%d)" % (entry["app"], win["title"], win["hwnd"]))
    print("elements     : %d in settled subtree" % payload["total"])
    print("clickable    : %d" % payload["clickable"])
    print("  safe       : %d" % payload["safe"])
    print("  unsafe     : %d" % sum(1 for c in controls
                                    if c["clickable"] and c["safety"] == "unsafe"))
    print("NO-SELECTOR  : %d  (these must generate explicit FAIL steps)" % payload["noSelector"])
    print("cache        : %s" % path)
    return payload


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--app", required=True)
    ap.add_argument("--timeout", type=int, default=25)
    args = ap.parse_args()
    run(args.app, args.timeout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
