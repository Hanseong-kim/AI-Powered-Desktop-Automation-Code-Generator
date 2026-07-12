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
               value=None, x=0, y=0, index=0,
               anchor_id="", anchor_path="", app_name=None, **extra):
    elem = {
        "name": name,
        "automationId": automation_id,
        "className": class_name,
        "controlType": control_type,
        "windowTitle": window_title,
        "xpath": f'//*[@AutomationId="{automation_id}"]' if automation_id else f'//*[@Name="{name}"]',
        "isInputField": control_type in ("Edit", "Document", "ComboBox"),
    }
    if anchor_id:
        # agent.py anchor capture (2026-07-11): element had no own id/name, so
        # the recorder resolved a relative XPath from the nearest ancestor
        # with a stable AutomationId.
        elem["anchorId"] = anchor_id
        elem["anchorPath"] = anchor_path
    ev = {
        "action": action,
        "element": elem,
        "timestamp": time.time(),
        "app": app_name or APP_NAME,
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
    # agent.py's _emit_click_from_press() emits a physical double-click as
    # click + click + doubleClick (never merges/drops — see agent.py:991-1009).
    # These two "constituent" clicks (same coords, emitted within ms of each
    # other) must be deduped away by dedupeDoubleClicks() at codegen time —
    # replaying them as separate _step()s is what turned a folder double-click
    # into a rename gesture (2026-07-08 VSCode "폴더 열기" dialog).
    make_event("click",       name="Result display", automation_id="CalculatorResults", class_name="TextBlock", control_type="Text", x=320, y=240, index=7),
    make_event("click",       name="Result display", automation_id="CalculatorResults", class_name="TextBlock", control_type="Text", x=320, y=240, index=8),
    make_event("doubleClick", name="Result display", automation_id="CalculatorResults", class_name="TextBlock", control_type="Text", x=320, y=240, index=9),
    # scroll now carries the scrollTarget the agent resolves by walking up to
    # the nearest ancestor exposing UIA ScrollPattern (2026-07-11) — replay
    # scrolls that container programmatically, no pixel coordinates.
    make_event("scroll",      name="",               automation_id="",             class_name="ApplicationFrameWindow", control_type="Window", value="-3", delta=-3, x=320, y=300, index=10,
               scrollTarget={"automationId": "", "className": "ScrollViewer", "name": "", "controlType": "Pane"}),
    # rightClick/drag are captured but OUT OF SCOPE for replay
    # (2026-07-10 stakeholder: event scope = Click/Type/DoubleClick/Scroll;
    # coordinate execution forbidden everywhere) — codegen must emit
    # scope-out comments, never osClick/osDrag calls.
    make_event("rightClick",  name="Result display", automation_id="CalculatorResults", class_name="TextBlock", control_type="Text", x=320, y=240, index=11),
    make_event("drag",        name="Result display", automation_id="CalculatorResults", class_name="TextBlock", control_type="Text",
               x=300, y=250, index=12,
               relX=100, relY=80, endX=500, endY=250, endRelX=300, endRelY=80,
               winLeft=200, winTop=170, winWidth=800, winHeight=600),
    # Element with NO id/name of its own — replay must use the anchor-based
    # relative XPath captured by the agent (never recorded coordinates).
    make_event("click",       name="", automation_id="", class_name="",
               control_type="Button", x=350, y=520, index=13,
               anchor_id="NumberPad", anchor_path="/Button[3]"),
]

# Multi-window (session-mode) scenario — distinct rootHwndHex values force
# needsSessionSwitching() true, exercising the SESSION_HEADER template that
# the Calculator scenario (simple mode) never touches. Closes the coverage
# gap flagged in CLAUDE.md §4 Next actions item 7.
SESSION_APP = "MockMulti"
SESSION_EXE = "C:\\mock\\multi.exe"
SESSION_EVENTS = [
    make_event("click", name="Open Settings", automation_id="btnOpen", class_name="Button",
               window_title="Main Window", app_name=SESSION_APP, x=100, y=100, index=1,
               rootHwndHex="A1B2",
               winLeft=0, winTop=0, winWidth=1024, winHeight=768),
    make_event("click", name="확인", automation_id="btnOk", class_name="Button",
               window_title="Settings Dialog", app_name=SESSION_APP, x=400, y=300, index=2,
               rootHwndHex="C3D4",
               winLeft=200, winTop=150, winWidth=600, winHeight=400),
    make_event("type", name="Server", automation_id="editServer", class_name="Edit",
               control_type="Edit", window_title="Settings Dialog", app_name=SESSION_APP,
               value="hello", index=3, rootHwndHex="C3D4"),
    make_event("scroll", name="", automation_id="", class_name="ScrollViewer",
               control_type="Pane", window_title="Settings Dialog", app_name=SESSION_APP,
               value="-2", delta=-2, x=400, y=350, index=4, rootHwndHex="C3D4",
               scrollTarget={"automationId": "optionList", "className": "ScrollViewer", "name": "", "controlType": "Pane"}),
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

    # 이전 세대 generate가 남긴 좌표 헬퍼를 재생성 시점에 지우는지(saveFiles의
    # OBSOLETE_FILES 정리) 검증 — 더미를 심어두고 generate 후 사라졌는지 본다.
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out_dir = os.path.join(repo_root, "generated-wdio", APP_NAME)
    os.makedirs(out_dir, exist_ok=True)
    for stale in ("osClick.ps1", "osDrag.ps1"):
        with open(os.path.join(out_dir, stale), "w", encoding="utf-8") as fh:
            fh.write("# dummy stale coordinate helper planted by mock_events.py\n")

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
    saved_paths = body.get("savedPaths", [])
    check(
        "osEscape.ps1 saved alongside the wdio output",
        any(str(p).endswith("osEscape.ps1") for p in saved_paths),
        f"savedPaths={saved_paths}",
    )
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
            "missing _warnings — silent session fallbacks would go unnoticed",
        )
        # 좌표 실행 전면 금지 (2026-07-10 스테이크홀더 지시, CLAUDE.md §3):
        # osClick/osClickRel/osDrag/osDragRel/osScrollRel 어떤 형태로도 생성 금지.
        for banned in ("osClick(", "osClickRel(", "osDrag(", "osDragRel(", "osScrollRel("):
            check(
                f"  {fname} has no coordinate replay call {banned}",
                banned not in content,
                f"found {banned} — coordinate-based execution is forbidden",
            )
        check(
            f"  {fname} scrolls via osScrollEl (ScrollPattern/PostMessage)",
            "osScrollEl(" in content,
            "missing osScrollEl — scroll must target the container, not pixels",
        )
        check(
            f"  {fname} uses the anchor-based relative XPath",
            '//*[@AutomationId="NumberPad"]/Button[3]' in content,
            "anchor click (no own id/name) did not render the anchor XPath",
        )
        check(
            f"  {fname} scope-outs drag and rightClick (not replayed)",
            "scope-out" in content and content.count("scope-out") >= 2,
            "drag/rightClick must render as scope-out comments "
            "(event scope = Click/Type/DoubleClick/Scroll)",
        )
        check(
            f"  {fname} wraps steps for popup Fail-and-Recover",
            "_step(" in content,
            "missing _step( wrapper — steps would not retry after a popup dismissal",
        )
        check(
            f"  {fname} has ESC recovery for buttonless failures",
            "osEscape(" in content and "esc-recovery:" in content,
            "missing osEscape()/esc-recovery — _step() can't back out of a rename "
            "edit-box or open menu when osDismissPopup finds no known button",
        )
        step_count = content.count("_step('")
        # 13 mock events: 2 "type" events skip (control_type=Text, not
        # editable); 2 constituent clicks before the doubleClick are merged
        # away by dedupeDoubleClicks(); rightClick + drag are scope-out.
        # Remaining steps: click(Five/Plus/Three/Equals)=4, doubleClick=1,
        # scroll=1, anchor click=1 -> 7.
        check(
            f"  {fname} step count (13 events -> 7 steps: dedupe + scope-out)",
            step_count == 7,
            f"got {step_count} _step(...) invocations",
        )
        check(
            f"  {fname} replays doubleClick via element re-click (no coords)",
            ":doubleClick" in content,
            "doubleClick step missing",
        )

    # The v2 popup-dismiss scoping and the owned-window pre-check live in the
    # saved .ps1 helpers, not the .js payload — read them back from disk
    # (utf-8-sig: saveFiles prepends a BOM so powershell -File parses Korean).
    def saved_helper(name):
        for p in saved_paths:
            if str(p).endswith(name):
                try:
                    with open(p, encoding="utf-8-sig") as fh:
                        return fh.read()
                except OSError:
                    return ""
        return ""

    dismiss = saved_helper("osDismissPopup.ps1")
    check(
        "osDismissPopup.ps1 takes -exclude (replay-driven windows protected)",
        "[string]$exclude" in dismiss and "$excludeSet" in dismiss,
        "missing -exclude scoping — dismisser can close the very dialog the "
        "failed step is about to retry against",
    )
    check(
        "osDismissPopup.ps1 requires dialog-shaped candidates (#32770 or owned)",
        "OwnerOf" in dismiss and "$qualifies" in dismiss,
        "same-PID main windows qualify as popups — single-process apps "
        "(VS Code) get another window's titlebar close button clicked",
    )
    winrect = saved_helper("osWindowRect.ps1")
    check(
        "osWindowRect.ps1 supports -ownerOnly (owned-window session skip)",
        "$ownerOnly" in winrect,
        "missing -ownerOnly — every owned dialog burns ~16s in a doomed "
        "scoped-session attempt before blacklisting",
    )
    scroll_ps1 = saved_helper("osScroll.ps1")
    check(
        "osScroll.ps1 scrolls via UIA ScrollPattern first",
        "ScrollPattern" in scroll_ps1,
        "missing ScrollPattern — scroll must be programmatic, not pixel injection",
    )
    check(
        "osScroll.ps1 falls back to PostMessageW (async), never SendMessageW",
        "PostMessageW" in scroll_ps1 and "SendMessageW" not in scroll_ps1,
        "PoC 2026-07-10: SendMessageW (sync) crashed charmap.exe — fallback "
        "must be PostMessageW",
    )
    check(
        "osScroll.ps1 has no physical pointer injection",
        "SetCursorPos" not in scroll_ps1 and "mouse_event" not in scroll_ps1,
        "found SetCursorPos/mouse_event — coordinate signal injection is forbidden",
    )
    check(
        "osClick.ps1 / osDrag.ps1 are no longer generated",
        not any(str(p).endswith(("osClick.ps1", "osDrag.ps1")) for p in saved_paths),
        f"coordinate-injection helpers still saved: {saved_paths}",
    )
    # savedPaths에 없는 것과 별개로, generate가 미리 심어둔 stale 파일을
    # 디스크에서 실제로 지웠는지 확인 (saveFiles의 OBSOLETE_FILES 정리).
    for stale in ("osClick.ps1", "osDrag.ps1"):
        check(
            f"stale {stale} removed from disk by generate",
            not os.path.exists(os.path.join(out_dir, stale)),
            f"{stale} still on disk — saveFiles obsolete-cleanup regressed",
        )


def step_wdio_generate_session():
    print("\n[9] Session-mode (multi-window) generation — SESSION_HEADER template")
    request("DELETE", "/api/events")
    for ev in SESSION_EVENTS:
        request("POST", "/api/events", ev)

    status, body = request("POST", "/api/generate", {
        "appName": SESSION_APP,
        "exePath": SESSION_EXE,
        "platform": PLATFORM,
    }, timeout=30)
    check("POST /api/generate (session) returns 200", status == 200, f"got {status}")
    if status != 200:
        check("(skipped session checks)", False, body.get("message", ""))
        return
    files = body.get("files", [])
    check("Two .js files returned (session)", len(files) == 2, f"got {len(files)}")
    for f in files:
        fname = f.get("filename", "")
        content = f.get("content", "")
        check(
            f"  {fname} uses HWND-segmented window sessions",
            "getWindowSession" in content and "launchApp" in content,
            "missing getWindowSession/launchApp — multi-window events must be "
            "replayed in their own window's session context",
        )
        check(
            f"  {fname} clicks via scoped-session element click",
            "_clickScoped(" in content,
            "missing _clickScoped — session-mode clicks must resolve XPath in "
            "the target window's session, no coordinates",
        )
        check(
            f"  {fname} types via scoped sendKeys",
            "_typeScoped(" in content,
            "missing _typeScoped — session-mode typing regressed",
        )
        check(
            f"  {fname} scrolls via osScrollEl with the window's hwnd",
            "osScrollEl(" in content and "_scrollHwnd(" in content,
            "missing osScrollEl/_scrollHwnd — session-mode scroll must target "
            "the dialog's container",
        )
        for banned in ("osClick(", "osClickRel(", "osDrag(", "osDragRel(", "osScrollRel("):
            check(
                f"  {fname} has no coordinate replay call {banned}",
                banned not in content,
                f"found {banned} — coordinate-based execution is forbidden",
            )
        check(
            f"  {fname} asserts on _failures",
            "expect(_failures).toEqual([])" in content,
            "missing _failures assert",
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
    step_wdio_generate_session()

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
