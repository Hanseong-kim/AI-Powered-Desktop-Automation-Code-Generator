"""
mock_events.py — Regression test for the Express bridge (server.js)
====================================================================
Simulates a Calculator recording session by POSTing synthetic events
directly to the server. No agent, no admin rights, no real app needed.

Usage:
    python agent/mock_events.py

With code generation (requires Groq key):
    set GROQ_API_KEY=gsk_...
    python agent/mock_events.py
"""

import json
import os
import sys
import time
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
               value=None, x=0, y=0, index=0):
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


def step_generate(api_key):
    print("\n[6] Code generation (Groq)")
    status, body = request("POST", "/api/generate", {
        "apiKey": api_key,
        "appName": APP_NAME,
        "platform": PLATFORM,
    }, timeout=60)
    check("POST /api/generate returns 200", status == 200, f"got {status}")
    if status == 200:
        check("ok == true", body.get("ok") is True, body.get("message", ""))
        files = body.get("files", [])
        check("Two files returned", len(files) == 2, f"got {len(files)}")
        for f in files:
            check(f"  {f['filename']} has content", bool(f.get("content")))
            check(f"  {f['filename']} starts with package", f.get("content", "").startswith("package"))
    else:
        check("(skipped file checks)", False, body.get("message", ""))


def step_missing_apikey():
    print("\n[7] Missing API key guard")
    status, body = request("POST", "/api/generate", {
        "appName": APP_NAME,
        "platform": PLATFORM,
    })
    check("Returns 400 when apiKey absent", status == 400, f"got {status}")


def step_generate_no_events():
    print("\n[8] Generate with empty event list")
    request("DELETE", "/api/events")
    status, body = request("POST", "/api/generate", {
        "apiKey": "dummy",
        "appName": APP_NAME,
        "platform": PLATFORM,
    })
    check("Returns 400 when no events", status == 400, f"got {status}")


def step_delete_event():
    print("\n[9] Event row delete (6 inject -> 1 delete -> 5 remain)")
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
    step_missing_apikey()
    step_generate_no_events()
    step_delete_event()

    # Re-load events for optional generation test
    step_clear_events()
    step_post_events()

    api_key = os.environ.get("GROQ_API_KEY", "")
    if api_key:
        step_generate(api_key)
    else:
        print("\n[6] Code generation - SKIPPED (set GROQ_API_KEY env var to enable)")

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
