"""
mock_events.py — Regression test for the Express bridge (server.js)
====================================================================
Simulates a Calculator recording session by POSTing synthetic events
directly to the server. No agent, no admin rights, no real app needed.

Usage:
    python agent/mock_events.py

Note: /api/generate is template-based (no LLM call) — code generation
always runs, no API key or environment variable needed.
"""

import io
import json
import os
import sys
import time

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
import urllib.error
import urllib.request

BASE = "http://localhost:3002"
APP_NAME = "Calculator"
PLATFORM = "Windows"

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"

_results = []


def check(label, ok, detail=""):
    tag = PASS if ok else FAIL
    line = f"  [{tag}] {label}"
    if detail:
        line += f"  ({detail})"
    print(line)
    _results.append(ok)


def request(method, path, body=None, timeout=8):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"} if data else {}
    req = urllib.request.Request(f"{BASE}{path}", data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())
    except Exception as e:
        return 0, {"error": str(e)}


# ---------------------------------------------------------------------------
# Synthetic event payload builder
# ---------------------------------------------------------------------------
def make_event(action, name="", automation_id="", class_name="",
               control_type="Button", window_title="Calculator",
               value=None, x=0, y=0, index=0, **extra):
    elem = {
        "name": name,
        "automationId": automation_id,
        "className": class_name,
        "controlType": control_type,
        "windowTitle": window_title,
        "xpath": f'//*[@AutomationId="{automation_id}"]' if automation_id else f'//*[@Name="{name}"]',
        "isInputField": control_type in ("Edit", "Document", "ComboBox"),
    }
    ev = {
        "action": action,
        "element": elem,
        "timestamp": time.time(),
        "app": APP_NAME,
        "platform": PLATFORM,
        "index": index,
        "x": x,
        "y": y,
    }
    if value is not None:
        ev["value"] = value
    ev.update(extra)  # e.g. relX/relY/endX/endY/endRelX/endRelY/winLeft/winTop/winWidth/winHeight
    return ev


# Realistic Calculator session: 5 + 3 =
MOCK_EVENTS = [
    make_event("click",       name="Five",           automation_id="num5Button",   class_name="Button", x=320, y=500, index=1),
    make_event("type",        name="Display",        automation_id="CalculatorResults", class_name="TextBlock", control_type="Text", value="5", index=2),
    make_event("click",       name="Plus",           automation_id="plusButton",   class_name="Button", x=440, y=500, index=3),
    make_event("click",       name="Three",          automation_id="num3Button",   class_name="Button", x=320, y=440, index=4),
    make_event("type",        name="Display",        automation_id="CalculatorResults", class_name="TextBlock", control_type="Text", value="3", index=5),
    make_event("click",       name="Equals",         automation_id="equalButton",  class_name="Button", x=440, y=560, index=6),
    make_event("doubleClick", name="Result display", automation_id="CalculatorResults", class_name="TextBlock", control_type="Text", x=320, y=240, index=7),
    make_event("scroll",      name="",               automation_id="",             class_name="ApplicationFrameWindow", control_type="Window", value="-3", x=320, y=300, index=8),
    make_event("rightClick",  name="Result display", automation_id="CalculatorResults", class_name="TextBlock", control_type="Text", x=320, y=240, index=9),
    make_event("drag",        name="Result display", automation_id="CalculatorResults", class_name="TextBlock", control_type="Text",
               x=300, y=250, index=10,
               relX=100, relY=80, endX=500, endY=250, endRelX=300, endRelY=80,
               winLeft=200, winTop=170, winWidth=800, winHeight=600),
]


# ---------------------------------------------------------------------------
# Test steps
# ---------------------------------------------------------------------------
def step_server_online():
    print("\n[1] Server connectivity")
    status, body = request("GET", "/api/status")
    check("GET /api/status returns 200", status == 200, f"got {status}")
    check("Response has eventCount field", "eventCount" in body)


def step_clear_events():
    print("\n[2] Clear existing events")
    status, body = request("DELETE", "/api/events")
    check("DELETE /api/events returns 200", status == 200)
    check("ok == true", body.get("ok") is True)


def step_post_events():
    print(f"\n[3] POST {len(MOCK_EVENTS)} mock events")
    for ev in MOCK_EVENTS:
        status, body = request("POST", "/api/events", ev)
        check(f"  POST event #{ev['index']} ({ev['action']})", status == 200 and body.get("ok"))


def step_verify_events():
    print("\n[4] Verify stored events")
    status, body = request("GET", "/api/events")
    check("GET /api/events returns 200", status == 200)
    count = len(body) if isinstance(body, list) else -1
    check(f"Event count == {len(MOCK_EVENTS)}", count == len(MOCK_EVENTS), f"got {count}")


def step_bad_exepath():
    print("\n[5] Bad exe path error handling")
    status, body = request("POST", "/api/start", {
        "appName": "Ghost",
        "exePath": "C:\\nonexistent\\ghost.exe",
        "platform": "Windows",
    })
    # Agent offline → 502, or agent online but exe missing → 400
    is_error = status in (400, 502)
    check("Non-200 on bad exe path", is_error, f"got {status}")
    has_msg = bool(body.get("message"))
    check("Response has error message", has_msg, body.get("message", ""))


def step_generate_no_events():
    print("\n[6] Generate with empty event list")
    request("DELETE", "/api/events")
    status, body = request("POST", "/api/generate", {
        "appName": APP_NAME,
        "platform": PLATFORM,
    })
    check("Returns 400 when no events", status == 400, f"got {status}")


def step_wdio_generate():
    print("\n[7] WebdriverIO JavaScript generation (template-based, no API key)")
    # Ensure events are loaded before generate call
    request("DELETE", "/api/events")
    for ev in MOCK_EVENTS:
        request("POST", "/api/events", ev)

    status, body = request("POST", "/api/generate", {
        "appName": APP_NAME,
        "platform": PLATFORM,
    }, timeout=30)
    check("POST /api/generate returns 200", status == 200, f"got {status}")
    if status != 200:
        check("(skipped file checks)", False, body.get("message", ""))
        return
    check("ok == true", body.get("ok") is True)
    check("folder field present", bool(body.get("folder")), f"got {body.get('folder')}")
    check("runCommand field present", bool(body.get("runCommand")))
    files = body.get("files", [])
    check("Two .js files returned", len(files) == 2, f"got {len(files)}")
    for f in files:
        fname = f.get("filename", "")
        content = f.get("content", "")
        check(f"  {fname} ends with .js", fname.endswith(".js"), f"got '{fname}'")
        check(f"  {fname} has content", bool(content.strip()))
        check(
            f"  {fname} contains waitForExist",
            "waitForExist" in content,
            "missing waitForExist — possible regression",
        )
        check(
            f"  {fname} asserts on _failures",
            "expect(_failures).toEqual([])" in content,
            "missing _failures assert — injection failures would go unnoticed",
        )
        check(
            f"  {fname} has no pause()",
            "pause(" not in content,
            f"found pause() calls — hardcoded waits are banned (CLAUDE.md)",
        )
        check(
            f"  {fname} tracks _warnings",
            "_warnings" in content,
            "missing _warnings — silent session/coord fallbacks would go unnoticed",
        )
        check(
            f"  {fname} replays the drag event",
            "osDragRel(" in content or "osDrag(" in content,
            "missing osDrag/osDragRel call — drag event fell through to a plain click",
        )
        check(
            f"  {fname} wraps steps for popup Fail-and-Recover",
            "_step(" in content,
            "missing _step( wrapper — steps would not retry after a popup dismissal",
        )


def step_delete_event():
    print("\n[8] Event row delete (6 inject -> 1 delete -> 5 remain)")
    request("DELETE", "/api/events")
    # Inject exactly 6 events
    for ev in MOCK_EVENTS[:6]:
        request("POST", "/api/events", ev)
    status, body = request("GET", "/api/events")
    check("6 events injected", status == 200 and len(body) == 6, f"got {len(body) if isinstance(body, list) else body}")

    # Delete array index 2 (3rd event)
    status, body = request("DELETE", "/api/events/2")
    check("DELETE /api/events/2 returns 200", status == 200, f"got {status}")
    check("eventCount == 5 in response", body.get("eventCount") == 5, f"got {body}")

    status, body = request("GET", "/api/events")
    count = len(body) if isinstance(body, list) else -1
    check("GET /api/events returns 5 events", count == 5, f"got {count}")

    # Out-of-range delete returns 400
    status, body = request("DELETE", "/api/events/999")
    check("Out-of-range delete returns 400", status == 400, f"got {status}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    print("=" * 54)
    print("  mock_events.py - QAForge pipeline regression test")
    print("=" * 54)
    print(f"  Target: {BASE}")

    step_server_online()
    step_clear_events()
    step_post_events()
    step_verify_events()
    step_bad_exepath()
    step_generate_no_events()
    step_delete_event()

    # Re-load events for generation test
    step_clear_events()
    step_post_events()
    step_wdio_generate()

    passed = sum(_results)
    total = len(_results)
    print(f"\n{'=' * 54}")
    print(f"  Result: {passed}/{total} checks passed")
    if passed < total:
        print("  Some checks FAILED — see above for details")
        sys.exit(1)
    else:
        print("  All checks PASSED")
    print("=" * 54)


if __name__ == "__main__":
    main()
