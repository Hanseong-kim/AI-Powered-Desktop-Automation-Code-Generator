"""
Tier 1 -- for every enumerated control, synthesize one click event, run it
through /api/generate, and audit the selector that comes out.

This is the fast lane: it needs the server but neither the app, the GUI, nor
admin rights, because it replays the *enumeration cache* rather than the app.
That is the same trick that lets mock_events.py run headless, and it is what
makes the fix loop practical -- a full pass over an app is seconds, so the
model can edit server.js and re-measure immediately.

WHAT IT CANNOT SEE: this bypasses agent.py entirely, so no capture-layer bug
can ever show up here (dead-element adoption, menu-snapshot races -- both real
2026-08-05 bugs). Tier 2 is the only thing that reaches those.

EVENT SAFETY: /api/events is global mutable state shared with whatever the
user is doing right now. This module always dumps the current events to
reports/_preserved_<ts>.json before its first DELETE, and prints how to put
them back. CLAUDE.md §1 documents the day a gate run destroyed a real
recording; that is the accident this guard exists to prevent.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import (  # noqa: E402
    request, make_event, check_helpers_defined, _strip_embedded_helpers,
    REPORTS_DIR, get_app, read_controls, write_report,
)

# Selector shapes that mean codegen gave up or produced something that cannot
# survive a relaunch. Each one traces to a real, documented bug class.
WILDCARD_SEL = re.compile(r'//\*\[@(ClassName|ControlType)="[^"]*"\]\s*$')
# The explicit FAIL step codegen emits when an event has nothing usable
# (server.js:5378). It must NOT be confused with the template's own
# `_failures.push('click-not-found:' + selector)` runtime handler, which is
# present verbatim in EVERY generated file -- matching that instead made all
# 5 controls in the first smoke run look like failures (2026-08-05).
NO_SELECTOR = re.compile(r"_failures\.push\('\d+:\w+:no-selector'\)")
# XPath / accessibility-id locator literals actually used as selectors.
SEL_LITERAL = re.compile(r"""['"`](//[^'"`]{3,300}|~[A-Za-z0-9_.\-]{1,80})['"`]""")
STATE_DEPENDENT_NAMES = ("열기", "닫기", "Open", "Close")


def preserve_events(tag):
    """Dump whatever is on the server before we clear it. Never skipped."""
    status, body = request("GET", "/api/events")
    if status != 200 or not isinstance(body, list) or not body:
        return None, 0
    os.makedirs(REPORTS_DIR, exist_ok=True)
    path = os.path.join(REPORTS_DIR, "_preserved_%s_%s.json"
                        % (tag, time.strftime("%Y%m%d-%H%M%S")))
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(body, fh, ensure_ascii=False, indent=2)
    return path, len(body)


def session_meta(entry, win):
    return {
        "action": "session_meta",
        "app": entry["appName"],
        "platform": entry["platform"],
        "timestamp": time.time(),
        "isElectron": False,
        "initialWindow": {
            "left": win["rect"][0], "top": win["rect"][1],
            "width": win["rect"][2] - win["rect"][0],
            "height": win["rect"][3] - win["rect"][1],
        },
    }


def event_for(ctrl, entry, win, index=1):
    """One synthetic click on this control, in the exact shape agent.py posts."""
    return make_event(
        "click",
        name=ctrl.get("name") or "",
        automation_id=ctrl.get("automationId") or "",
        class_name=ctrl.get("className") or "",
        control_type=ctrl.get("controlType") or "Button",
        window_title=win["title"],
        app_name=entry["appName"],
        x=(ctrl.get("centre") or [0, 0])[0],
        y=(ctrl.get("centre") or [0, 0])[1],
        index=index,
        relX=(ctrl.get("centre") or [0, 0])[0] - win["rect"][0],
        relY=(ctrl.get("centre") or [0, 0])[1] - win["rect"][1],
        winLeft=win["rect"][0], winTop=win["rect"][1],
        winWidth=win["rect"][2] - win["rect"][0],
        winHeight=win["rect"][3] - win["rect"][1],
    )


def generate_for(ctrl, entry, win):
    request("DELETE", "/api/events")
    request("POST", "/api/events", session_meta(entry, win))
    request("POST", "/api/events", event_for(ctrl, entry, win))
    status, body = request("POST", "/api/generate", {
        "appName": entry["appName"],
        "exePath": entry["exePath"],
        "platform": entry["platform"],
    }, timeout=60)
    return status, body


def audit(ctrl, files):
    """Findings for one control. Empty list == codegen handled it correctly."""
    findings = []
    by_id = next((f for f in files if f["filename"].endswith("TestById.js")), None)
    if not by_id:
        return [{"kind": "NO-OUTPUT", "detail": "generate returned no TestById.js"}]

    js = by_id["content"]
    body = _strip_embedded_helpers(js)

    # Every _step label + every selector literal the file actually uses.
    steps = re.findall(r"_step\('([^']*)'", body)
    sels = SEL_LITERAL.findall(body)
    joined = " ".join(sels)

    expected_fail = "NO-SELECTOR" in ctrl["flags"]
    emitted_fail = bool(NO_SELECTOR.search(body))

    if expected_fail and not emitted_fail:
        findings.append({
            "kind": "MISSING-FAIL-STEP",
            "detail": "control has no name/automationId/className, so codegen "
                      "must emit an explicit FAIL step (CLAUDE.md §3), but the "
                      "generated file contains no no-selector marker",
        })
    if not expected_fail and emitted_fail:
        findings.append({
            "kind": "UNEXPECTED-FAIL-STEP",
            "detail": "control IS addressable (%s) but codegen emitted a "
                      "no-selector step" % _identity(ctrl),
        })
    if not steps and not expected_fail:
        findings.append({
            "kind": "EVENT-DROPPED",
            "detail": "the event produced no _step() at all -- it was filtered "
                      "away somewhere in the codegen pipeline",
        })

    for s in sels:
        if WILDCARD_SEL.match(s.strip()):
            findings.append({"kind": "WILDCARD-SELECTOR", "detail": s.strip()})

    if "HWND-AS-AUTOMATIONID" in ctrl["flags"]:
        aid = ctrl["automationId"]
        if ('@AutomationId="%s"' % aid) in joined or ("~" + aid) in joined:
            findings.append({
                "kind": "HWND-ID-IN-SELECTOR",
                "detail": "automationId %r is this control's own window handle "
                          "and is reassigned every launch, but it reached the "
                          "selector anyway" % aid,
            })

    # NOTE: duplicate-automationId ambiguity is deliberately NOT judged here.
    # server.js:4741 computes `ambiguousIds` across the events of one
    # recording, so a single-event generate can never exhibit it -- flagging it
    # per control would be an artifact of this harness's own isolate mode, not
    # a codegen defect. It is reported once per app by static_risks() instead.

    name = ctrl.get("name") or ""
    if name in STATE_DEPENDENT_NAMES and ('@Name="%s"' % name) in joined:
        findings.append({
            "kind": "STATE-DEPENDENT-NAME",
            "detail": "%r flips with the control's own state, so a selector "
                      "built from it only matches in one state" % name,
        })
    return findings


def static_risks(cache):
    """App-level risks visible from the enumeration alone -- no generate needed.

    These are properties of the SCREEN, not of any one generated file, so they
    are reported once per app rather than per control.
    """
    risks = []
    by_id = {}
    for c in cache["controls"]:
        aid = c.get("automationId") or ""
        if aid and c["clickable"]:
            by_id.setdefault(aid, []).append(c)

    for aid, group in sorted(by_id.items()):
        if len(group) < 2:
            continue
        named = {g.get("name") or "" for g in group}
        # server.js:4741 flags an id as ambiguous only when it maps to more
        # than one distinct NON-EMPTY name. A pair of nameless siblings on the
        # same id therefore slips through and both get a bare `~id` selector.
        detectable = len([n for n in named if n]) > 1
        risks.append({
            "kind": "DUPLICATE-AUTOMATIONID",
            "automationId": aid,
            "count": len(group),
            "controls": ["%s/%s/%r" % (g["controlType"], g.get("className") or "-",
                                       g.get("name") or "") for g in group],
            "detectableByCodegen": detectable,
            "detail": (
                "%d clickable controls share automationId %r. "
                % (len(group), aid)
            ) + (
                "codegen's ambiguousIds set will catch this and AND in the Name."
                if detectable else
                "codegen CANNOT see this: ambiguousIds (server.js:4741) keys on "
                "distinct non-empty Names, and these carry none, so each emits a "
                "bare `~%s` that resolves to whichever element is found first. "
                "This is the ComboBoxEx 'two controls stacked on one rect' shape "
                "from CLAUDE.md §5, where only the inner control is drivable." % aid
            ),
        })
    return risks


def _identity(c):
    return "aid=%r name=%r class=%r" % (c.get("automationId"), c.get("name"),
                                        c.get("className"))


def node_check(path):
    r = subprocess.run(["node", "--check", path], capture_output=True, text=True)
    return r.returncode == 0, (r.stderr or r.stdout).strip()[:300]


def run(app, limit=None, include_unsafe=False):
    entry = get_app(app)
    cache = read_controls(entry["app"])
    win = cache["window"]

    targets = [c for c in cache["controls"] if c["clickable"]]
    if not include_unsafe:
        targets = [c for c in targets if c["safety"] != "unsafe"]
    if limit:
        targets = targets[:limit]

    preserved, n_pre = preserve_events(entry["app"])
    if preserved:
        print("[guard] %d pre-existing event(s) on the server were saved to\n"
              "        %s\n"
              "        restore with: POST /api/events for each entry, or use "
              "the UI's restore control." % (n_pre, preserved))

    results, gen_dir = [], None
    t0 = time.time()
    for i, ctrl in enumerate(targets, 1):
        status, body = generate_for(ctrl, entry, win)
        if status != 200 or not body.get("ok"):
            results.append({"control": _identity(ctrl), "key": ctrl["key"],
                            "findings": [{"kind": "GENERATE-FAILED",
                                          "detail": "status=%s %s" % (status, str(body)[:200])}]})
            continue
        files = body.get("files", [])
        findings = audit(ctrl, files)
        if gen_dir is None:
            gen_dir = os.path.join("generated-wdio", entry["appName"])
        results.append({
            "control": _identity(ctrl),
            "key": ctrl["key"],
            "controlType": ctrl["controlType"],
            "name": ctrl.get("name"),
            "flags": ctrl["flags"],
            "findings": findings,
        })
        if i % 20 == 0:
            print("  ... %d/%d" % (i, len(targets)))

    # One syntax check on the last build -- the template is identical across
    # controls, so checking every one would be 78 redundant node spawns.
    syntax_ok, syntax_err = True, ""
    if gen_dir:
        p = os.path.join(gen_dir, "%sTestById.js" % entry["appName"])
        if os.path.exists(p):
            syntax_ok, syntax_err = node_check(p)
            with open(p, encoding="utf-8") as fh:
                check_helpers_defined(os.path.basename(p), fh.read())

    flagged = [r for r in results if r["findings"]]
    risks = static_risks(cache)
    payload = {
        "app": entry["app"],
        "appName": entry["appName"],
        "window": win,
        "tested": len(results),
        "flagged": len(flagged),
        "staticRisks": risks,
        "elapsedSec": round(time.time() - t0, 1),
        "syntaxOk": syntax_ok,
        "syntaxError": syntax_err,
        "preservedEvents": preserved,
        "results": results,
    }
    path = write_report(entry["app"], "codegen", payload)

    print("\ntested   : %d controls in %.1fs" % (payload["tested"], payload["elapsedSec"]))
    print("flagged  : %d" % payload["flagged"])
    kinds = {}
    for r in flagged:
        for f in r["findings"]:
            kinds[f["kind"]] = kinds.get(f["kind"], 0) + 1
    for k, v in sorted(kinds.items(), key=lambda kv: -kv[1]):
        print("   %-24s %d" % (k, v))
    blind = [r for r in risks if not r["detectableByCodegen"]]
    if risks:
        print("static risks: %d duplicate-automationId group(s), %d of them "
              "invisible to codegen" % (len(risks), len(blind)))
        for r in blind:
            print("   ~%-8s %s" % (r["automationId"], " + ".join(r["controls"])))
    print("syntax   : %s%s" % ("OK" if syntax_ok else "FAILED",
                               "" if syntax_ok else " -- " + syntax_err))
    print("report   : %s" % path)
    return payload


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--app", required=True)
    ap.add_argument("--limit", type=int, default=None,
                    help="only the first N controls (smoke test)")
    ap.add_argument("--include-unsafe", action="store_true",
                    help="also audit controls the safety filter rejected "
                         "(no clicking happens in tier 1, so this is safe)")
    args = ap.parse_args()
    run(args.app, args.limit, args.include_unsafe)
    return 0


if __name__ == "__main__":
    sys.exit(main())
