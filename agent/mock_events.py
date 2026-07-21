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
               anchor_id="", anchor_path="", app_name=None,
               expand_collapse=False, **extra):
    elem = {
        "name": name,
        "automationId": automation_id,
        "className": class_name,
        "controlType": control_type,
        "windowTitle": window_title,
        "xpath": f'//*[@AutomationId="{automation_id}"]' if automation_id else f'//*[@Name="{name}"]',
        "isInputField": control_type in ("Edit", "Document", "ComboBox"),
        "expandCollapse": expand_collapse,
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
    # Revisit case (2026-07-16, multi-window segmenting fix): the dialog
    # closes and the next click lands back on the ORIGINAL window (hwnd
    # A1B2) — the segment-boundary detector must fire again on the way
    # back, not just on the one-way A1B2->C3D4 transition (Hamza review
    # feedback: "actions after navigating back should still be grouped
    # under the right window").
    make_event("click", name="Cancel", automation_id="btnCancel", class_name="Button",
               window_title="Main Window", app_name=SESSION_APP, x=150, y=120, index=5,
               rootHwndHex="A1B2",
               winLeft=0, winTop=0, winWidth=1024, winHeight=768),
    # ExpandCollapsePattern + session mode (2026-07-16, bug B fix): opening a
    # File-menu-style MenuItem and selecting an item within it must still
    # replay via osExpandCollapse() even in session mode, where _appHwnd
    # doesn't exist (must use _hwndCache[_mainTitleFrag] instead) — this was
    # silently skipped entirely before the fix (FileZilla GUI repro: File
    # menu opened but the target menu item was never actually clicked, so
    # the Site Manager dialog never opened during replay). Same window as
    # the revisit event above (A1B2) so it doesn't add a new segment-boundary
    # switch — the switch-count assertions elsewhere in this scenario stay
    # valid (still exactly 3: A1B2 -> C3D4 -> A1B2).
    make_event("click", name="File", automation_id="menuFile", class_name="MenuItem",
               control_type="MenuItem", window_title="Main Window", app_name=SESSION_APP,
               x=50, y=20, index=6, rootHwndHex="A1B2", expand_collapse=True,
               winLeft=0, winTop=0, winWidth=1024, winHeight=768),
    make_event("click", name="Site Manager", automation_id="menuSiteManager", class_name="MenuItem",
               control_type="MenuItem", window_title="Main Window", app_name=SESSION_APP,
               x=60, y=45, index=7, rootHwndHex="A1B2",
               winLeft=0, winTop=0, winWidth=1024, winHeight=768),
]

# Title-collision scenario (2026-07-16, multi-window segmenting fix) — two
# DIFFERENT windows sharing the exact same literal title text (confirmed
# real-world case, 2026-07-15 "버그2": 7-Zip's main file-list window and its
# "압축 대상 추가" dialog are BOTH just titled "7-Zip"). getWindowSession()'s
# title-keyed cache can't tell them apart by title alone; the switch-step's
# _switchWindow() must force a fresh lookup on every hwnd-boundary crossing
# even when the title string is identical, or replay silently reuses a dead
# session/hwnd from the wrong window (STEP N+ click-not-found).
COLLISION_APP = "MockCollision"
COLLISION_EXE = "C:\\mock\\collision.exe"
COLLISION_EVENTS = [
    make_event("click", name="Extract", automation_id="btnExtract", class_name="Button",
               window_title="7-Zip", app_name=COLLISION_APP, x=100, y=100, index=1,
               rootHwndHex="E1E1",
               winLeft=0, winTop=0, winWidth=1024, winHeight=768),
    make_event("click", name="OK", automation_id="btnOk", class_name="Button",
               window_title="7-Zip", app_name=COLLISION_APP, x=400, y=300, index=2,
               rootHwndHex="F2F2",
               winLeft=200, winTop=150, winWidth=600, winHeight=400),
    # Back to the main window — SAME literal title ("7-Zip") as event 1, and
    # the SAME hwnd (E1E1) as event 1, but a DIFFERENT hwnd than the
    # immediately preceding event (F2F2). Must still trigger a switch. Plain
    # Button (not ListItem) so this goes through the ordinary _clickScoped
    # path that getWindowSession()/_switchWindow() actually govern.
    make_event("click", name="Refresh", automation_id="btnRefresh", class_name="Button",
               window_title="7-Zip", app_name=COLLISION_APP,
               x=120, y=200, index=3, rootHwndHex="E1E1",
               winLeft=0, winTop=0, winWidth=1024, winHeight=768),
]

# Delayed-rootHwndHex dialog scenario (2026-07-17, real FileZilla GUI run) —
# agent.py's PID self-heal lets a click through with the correct windowTitle
# the INSTANT a new dialog's PID matches the target app, but rootHwndHex
# tagging lags a few events behind until the background watcher formally
# registers the hwnd. server.js's window-segment boundary detection (both
# the runtime `_switchWindow()` gate and the `[Wn]` banner pre-pass) used to
# key ONLY off rootHwndHex/newWindowSegment, so this lag meant the first
# few clicks inside a freshly-opened dialog were silently attributed to the
# PREVIOUS window — no `switch to window:` step ever got generated for that
# dialog, and its banner showed under the wrong window section. Real capture
# had 3 TreeItem clicks with windowTitle="사이트 관리자" but rootHwndHex=None
# before rootHwndHex finally appeared on the 4th event.
DELAYED_HWND_APP = "MockDelayedHwnd"
DELAYED_HWND_EXE = "C:\\mock\\delayedhwnd.exe"
DELAYED_HWND_EVENTS = [
    make_event("click", name="Open", automation_id="btnOpen", class_name="Button",
               window_title="Main Window", app_name=DELAYED_HWND_APP, x=100, y=100, index=1,
               rootHwndHex="AAAA", winLeft=0, winTop=0, winWidth=1024, winHeight=768),
    # Dialog opens. windowTitle flips immediately (PID self-heal); rootHwndHex
    # stays None for these two clicks, exactly like the real capture.
    make_event("click", name="Field1", automation_id="", class_name="TreeItem",
               window_title="Dialog", app_name=DELAYED_HWND_APP, x=200, y=200, index=2),
    make_event("click", name="Field2", automation_id="", class_name="TreeItem",
               window_title="Dialog", app_name=DELAYED_HWND_APP, x=210, y=210, index=3),
    # rootHwndHex finally shows up here (watcher caught up).
    make_event("click", name="OkButton", automation_id="btnOk", class_name="Button",
               window_title="Dialog", app_name=DELAYED_HWND_APP, x=220, y=220, index=4,
               rootHwndHex="BBBB", newWindowSegment=True,
               winLeft=200, winTop=150, winWidth=400, winHeight=300),
]

# Redundant-trigger-click ComboBox scenario (2026-07-17, real FileZilla GUI
# run — Site Manager's "배경색(B):" color combo needed 3 physical clicks
# before it actually opened). mergeExpandCollapseClicks() paired an
# expandCollapse trigger with whatever event came right after it, without
# checking whether that "next" event was itself just another re-click of
# the SAME trigger rather than a real item — so click #1 got merged with
# click #2 (itemName = the combo's own label) instead of with the real
# item ("빨강") that came after click #3. This app's events replicate that
# exact pattern with a plain Button trigger (name="Combo") to isolate the
# bug from ComboBox-specific behavior.
EXPAND_REDUNDANT_APP = "MockExpandRedundant"
EXPAND_REDUNDANT_EVENTS = [
    make_event("click", name="Combo", automation_id="5999", class_name="ComboBox",
               control_type="ComboBox", app_name=EXPAND_REDUNDANT_APP,
               expand_collapse=True, index=1),
    make_event("click", name="Combo", automation_id="5999", class_name="ComboBox",
               control_type="ComboBox", app_name=EXPAND_REDUNDANT_APP,
               expand_collapse=True, index=2),
    make_event("click", name="Combo", automation_id="5999", class_name="ComboBox",
               control_type="ComboBox", app_name=EXPAND_REDUNDANT_APP,
               expand_collapse=True, index=3),
    make_event("click", name="Red", automation_id="", class_name="ListItem",
               control_type="ListItem", app_name=EXPAND_REDUNDANT_APP, index=4),
    # A normal (non-redundant) MenuItem->MenuItem merge right after, to prove
    # the fix doesn't touch the existing correct-merge path.
    make_event("click", name="File", automation_id="", class_name="MenuItem",
               control_type="MenuItem", app_name=EXPAND_REDUNDANT_APP,
               expand_collapse=True, index=5),
    make_event("click", name="Open", automation_id="mnuOpen", class_name="MenuItem",
               control_type="MenuItem", app_name=EXPAND_REDUNDANT_APP,
               expand_collapse=True, index=6),
]

# Native Win32 dialog scenario (2026-07-13, PuTTY GUI failure follow-up) —
# exercises the SLOT_INDEX_CONTROL_TYPES carve-out in wdioSelectorById/
# wdioSelectorByClass: numeric AutomationIds are STABLE resource IDs on
# ordinary Win32 controls (Button/CheckBox/...) but runtime slot indices on
# virtualized ListItem/TreeItem/DataItem rows — only the latter should still
# be rejected in favor of a Name-based selector.
NATIVE_APP = "MockNative"
NATIVE_EVENTS = [
    make_event("click", name="System menu appears on ALT-Space", automation_id="1049",
               class_name="Button", control_type="CheckBox", window_title="Native Dialog",
               app_name=NATIVE_APP, x=707, y=419, index=1),
    make_event("click", name="Selection", automation_id="6",
               class_name="TreeItem", control_type="TreeItem", window_title="Native Dialog",
               app_name=NATIVE_APP, x=590, y=416, index=2),
    # ExpandCollapsePattern scenario (2026-07-13, poc/diag_expandcollapse.py):
    # opening a ComboBox dropdown then picking an item is captured as TWO
    # click events — codegen must merge them into a single osExpandCollapse()
    # call (mergeExpandCollapseClicks), not two separate _step()s.
    make_event("click", name="Proxy type:", automation_id="1044",
               class_name="ComboBox", control_type="ComboBox", window_title="Native Dialog",
               app_name=NATIVE_APP, x=1058, y=378, index=3, expand_collapse=True),
    make_event("click", name="SOCKS 5", automation_id="",
               class_name="", control_type="ListItem", window_title="Native Dialog",
               app_name=NATIVE_APP, x=1051, y=410, index=4),
    # TreeItem +/- toggle: expandCollapse=true but NOT followed by a
    # ComboBox/MenuItem-style item-selection click — must stay a standalone
    # osExpandCollapse() call with itemName=null, and must NOT swallow the
    # unrelated click that happens to follow it.
    make_event("click", name="Window", automation_id="",
               class_name="TreeItem", control_type="TreeItem", window_title="Native Dialog",
               app_name=NATIVE_APP, x=678, y=449, index=5, expand_collapse=True),
    make_event("click", name="Data", automation_id="",
               class_name="TreeItem", control_type="TreeItem", window_title="Native Dialog",
               app_name=NATIVE_APP, x=722, y=484, index=6),
    # Cross-window click (2026-07-13, PuTTY "Remote character set:" follow-up):
    # a plain Button (no ExpandCollapsePattern) opens a dropdown list that
    # renders in a SEPARATE top-level window (Win32 class "ComboLBox") — the
    # WinAppDriver session (scoped to the main window) can't see it. codegen
    # must detect this from the event's own captured window geometry
    # (winLeft/Top/Width/Height differing from the main window recorded in
    # session_meta) and route through osScopedInvoke(), not a plain
    # browser.$(sel) click.
    # name is deliberately a state-dependent label (mirrors the real PuTTY
    # capture: a Win32 ComboBox dropdown arrow's accessible Name toggles
    # "open"/"close" by list-open state, and the worker-thread hit-test always
    # runs AFTER the click already opened the list — so capture only ever sees
    # the "open" name, which never matches at replay time when the control is
    # still closed). codegen must not trust this name when automationId is
    # present (2026-07-14, PuTTY Translation "Remote character set:" combo:
    # trusting it made osScopedInvoke's trigger search match nothing, so the
    # trigger was silently never invoked and the dropdown never opened).
    make_event("click", name="close", automation_id="DropDown",
               class_name="", control_type="Button", window_title="Native Dialog",
               app_name=NATIVE_APP, x=790, y=410, index=7,
               winLeft=400, winTop=200, winWidth=800, winHeight=600),
    make_event("click", name="Some Encoding", automation_id="",
               class_name="", control_type="ListItem", window_title="Native Dialog",
               app_name=NATIVE_APP, x=420, y=560, index=8,
               winLeft=350, winTop=520, winWidth=300, winHeight=200),
    # Merge-across-scroll (2026-07-14, PuTTY "Remote character set:" re-open→
    # scroll→select): a main-window trigger (DropDown arrow) + an intervening
    # scroll inside the opened ComboLBox + a cross-window item click must merge
    # into ONE osScopedInvoke(item, trigger), DROPPING the scroll (COM FindFirst
    # locates the item regardless of scroll position). Otherwise the trigger
    # survives as its own click and, in ByClass, its captured Name resolves to
    # //Button[@Name="close"] — matching the titlebar Close (X) button — which
    # closes the app (confirmed 2026-07-14: PuTTY ByClass STEP 5 killed PuTTY).
    make_event("click", name="close", automation_id="DropDown",
               class_name="", control_type="Button", window_title="Native Dialog",
               app_name=NATIVE_APP, x=790, y=378, index=9,
               winLeft=400, winTop=200, winWidth=800, winHeight=600),
    make_event("scroll", name="Latin-1", automation_id="",
               class_name="ComboLBox", control_type="List", window_title="Native Dialog",
               app_name=NATIVE_APP, x=1051, y=450, index=10, value="6", delta=6,
               scrollTarget={"automationId": "", "className": "ComboLBox",
                             "name": "Charset", "controlType": "List"},
               winLeft=350, winTop=520, winWidth=300, winHeight=200),
    make_event("click", name="UTF-8 Item", automation_id="",
               class_name="", control_type="ListItem", window_title="Native Dialog",
               app_name=NATIVE_APP, x=420, y=430, index=11,
               winLeft=350, winTop=520, winWidth=300, winHeight=200),
    # Reused numeric AutomationId across DIFFERENT fields (2026-07-17,
    # FileZilla Site Manager GUI failure: automationId="5999" is shared by
    # ~12 Edit fields — Host/Port/User/Password/... — each with a distinct
    # Name). A bare accessibility-id selector ('~5999') always resolves to
    # the FIRST matching field, so FileZillaTestById.js's Host/Port TYPE
    # steps failed with "target not found" while the SAME fields' cross-window
    # CLICK steps (which build {automationId,className,name} directly,
    # bypassing wdioSelectorById) succeeded — proving the data (a distinct
    # Name per field) was always present in the capture; only the ById
    # selector-builder was throwing it away. codegen must detect the reuse
    # and AND the Name into the selector (same fix class as the PuTTY
    # 2026-07-13 5차 combo/radio id collision), while lone/non-reused numeric
    # ids (e.g. "1049" above) must keep resolving to the bare '~id' form.
    make_event("click", name="Host:", automation_id="5999",
               class_name="Edit", control_type="Edit", window_title="Native Dialog",
               app_name=NATIVE_APP, x=300, y=200, index=12),
    make_event("click", name="Port:", automation_id="5999",
               class_name="Edit", control_type="Edit", window_title="Native Dialog",
               app_name=NATIVE_APP, x=300, y=260, index=13),
    make_event("type", name="Host:", automation_id="5999",
               class_name="Edit", control_type="Edit", window_title="Native Dialog",
               app_name=NATIVE_APP, value="host.example.com", x=300, y=200, index=14),
]
NATIVE_SESSION_META = {
    "action": "session_meta",
    "app": NATIVE_APP,
    "platform": PLATFORM,
    "timestamp": time.time(),
    "isElectron": False,
    "initialWindow": {"left": 400, "top": 200, "width": 800, "height": 600},
}

# Launch-animation rect mismatch (2026-07-21, real Calculator GUI repro):
# session_meta.initialWindow is captured the instant _discover_target_windows()
# first sees the window's hwnd, which for a UWP app can be mid-reveal-animation
# — its rect hasn't settled to the resting geometry the user actually clicks
# against yet. Reproduced identically across 3 independent real Calculator
# recordings: initialWindow always left=0, every click's own winLeft/Top/
# Width/Height always differ by the same fixed offset (here: left 0->1502,
# width +18, height +10) even though it's the SAME single window the whole
# time — a naive recordedRect picked from session_meta alone misclassifies
# every single click in simple (non-Electron, single-window) mode as
# "(cross-window)", forcing 100% of clicks onto the slower COM-based
# osScopedInvoke path instead of a plain browser click.
ANIM_APP = "MockAnimSettle"
ANIM_SESSION_META = {
    "action": "session_meta",
    "app": ANIM_APP,
    "platform": PLATFORM,
    "timestamp": time.time(),
    "isElectron": False,
    "initialWindow": {"left": 0, "top": 1, "width": 400, "height": 665},
}
ANIM_EVENTS = [
    make_event("click", name="Seven", automation_id="num7Button", class_name="Button",
               window_title="Calculator", app_name=ANIM_APP, x=1520, y=440, index=1,
               winLeft=1502, winTop=0, winWidth=418, winHeight=675),
    make_event("click", name="Eight", automation_id="num8Button", class_name="Button",
               window_title="Calculator", app_name=ANIM_APP, x=1560, y=440, index=2,
               winLeft=1502, winTop=0, winWidth=418, winHeight=675),
]

# Nested-dialog DropDown state-name bug (2026-07-21, real 7-Zip GUI repro:
# Tools -> Options -> "Language:" ComboBox). Three levels deep (main window
# -> Options dialog -> ComboLBox popup): the Options dialog itself already
# has different geometry than the recorded main-window rect, so the DropDown
# arrow's OWN click never satisfies mergeCrossWindowTriggerClicks's
# "!isCrossWindowEvent(e)" trigger prerequisite and falls through as a
# standalone (unmerged) cross-window click instead — the ONLY code path
# that previously dropped the trigger's state-dependent captured Name
# (PuTTY 2026-07-14 fix) was triggerTarget, built only for the MERGED case.
# Real capture: automationId="DropDown", name="닫기" ("Close" — only true
# while the list is already open; at replay start the real name is "열기"/
# Open), so an AND-condition on both fields matches nothing.
NESTED_DROPDOWN_APP = "MockNestedDropdown"
NESTED_DROPDOWN_EVENTS = [
    make_event("click", name="", automation_id="", class_name="",
               window_title="Main", app_name=NESTED_DROPDOWN_APP, x=100, y=100, index=1,
               winLeft=0, winTop=0, winWidth=800, winHeight=600),
    # Options dialog opens with DIFFERENT geometry than the main window —
    # every event captured inside it is cross-window relative to recordedRect,
    # including the DropDown trigger itself.
    make_event("click", name="닫기", automation_id="DropDown", class_name="",
               window_title="Options", app_name=NESTED_DROPDOWN_APP, x=400, y=200, index=2,
               winLeft=300, winTop=100, winWidth=400, winHeight=300),
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
    for stale in ("osClick.ps1", "osDrag.ps1", "osScopedInvoke.ps1", "osScroll.ps1", "osExpandCollapse.ps1", "wdio.conf.js"):
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
    run_command = body.get("runCommand", "")
    check(
        "runCommand uses standalone `node <file>.js`, not `npx wdio run`",
        run_command.startswith("cd generated-wdio/") and "node " in run_command and "npx wdio" not in run_command,
        f"got '{run_command}' — the setup-dependency gap (record -> generate -> "
        "node thefile.js, no harness assembly) means the advertised run "
        "command must not require the WDIO CLI",
    )
    files = body.get("files", [])
    check("Two .js files returned", len(files) == 2, f"got {len(files)}")
    saved_paths = body.get("savedPaths", [])
    check(
        "osEscape.ps1 saved alongside the wdio output",
        any(str(p).endswith("osEscape.ps1") for p in saved_paths),
        f"savedPaths={saved_paths}",
    )
    check(
        "package.json saved alongside the wdio output (self-describing standalone folder)",
        any(str(p).endswith("package.json") for p in saved_paths),
        f"savedPaths={saved_paths}",
    )
    for f in files:
        fname = f.get("filename", "")
        content = f.get("content", "")
        check(f"  {fname} ends with .js", fname.endswith(".js"), f"got '{fname}'")
        check(f"  {fname} has content", bool(content.strip()))
        check(
            f"  {fname} clicks via _clickBySid (single _appSid, no browser.$)",
            "_clickBySid(_appSid" in content,
            "simple-mode click must resolve XPath via the raw Appium REST "
            "session this file opens itself — no WDIO `browser` global "
            "(2026-07-17 standalone execution)",
        )
        check(
            f"  {fname} asserts on _failures via process.exitCode (no Jasmine expect)",
            "process.exitCode = 1" in content and "expect(_failures)" not in content,
            "missing the standalone pass/fail exit-code check, or a leftover "
            "Jasmine expect() that would crash under plain `node` (no "
            "injected `expect` global without the WDIO/Jasmine runner)",
        )
        check(
            f"  {fname} is a standalone script (no describe/it/browser.*)",
            "describe(" not in content and "browser." not in content and "async function run()" in content,
            "generated file must run under plain `node <file>.js` — no "
            "Jasmine describe/it wrapper and no WDIO `browser` global "
            "(2026-07-17: setup-dependency gap)",
        )
        check(
            f"  {fname} self-starts Appium (ensureAppium) and opens its own session",
            "async function ensureAppium()" in content and "_createSession(" in content,
            "standalone file must start/reuse Appium itself and create its "
            "own session — previously this was WDIO's job via wdio.conf.js",
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
        check(
            f"  {fname} detects ESC recovery closing the app itself",
            "esc-recovery-closed-app:" in content,
            "ESC == Cancel on dialog-based main windows (e.g. PuTTY "
            "Configuration) — without this guard, _step() retries into a "
            "no-such-window cascade instead of surfacing the real failure "
            "(PuTTY 2026-07-13)",
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
        check(
            f"  {fname} calls osScopedInvoke.py via python, not the old .ps1",
            'osScopedInvoke.py' in content and 'python "' in content
            and 'osScopedInvoke.ps1' not in content,
            "osScopedInvoke() wrapper still shells out to the old managed-UIA "
            "PowerShell helper instead of the COM/Python replacement "
            "(PuTTY 2026-07-14: managed UIA can't see Button/ComboBox "
            "internals on native Win32 dialogs)",
        )
        check(
            f"  {fname} calls osScroll.py via python, not the old .ps1",
            'osScroll.py' in content and 'python "' in content
            and 'osScroll.ps1' not in content,
            "osScroll() wrapper still shells out to the old managed-UIA "
            "PowerShell helper instead of the COM/Python replacement "
            "(PuTTY 2026-07-14: FromHandle remained unreliable even with a "
            "retry, on a re-verification GUI run)",
        )
        check(
            f"  {fname} calls osExpandCollapse.py via python, not the old .ps1",
            'osExpandCollapse.py' in content and 'osExpandCollapse.ps1' not in content,
            "osExpandCollapse() wrapper still shells out to the old managed-UIA "
            "PowerShell helper — managed UIA is blind to legacy SysTreeView32 "
            "TreeItems, so expand/collapse of a tree node always failed "
            "(PuTTY 2026-07-14, poc/FINDINGS.md)",
        )
        check(
            f"  {fname} skips ESC when the main dialog itself holds the foreground",
            "osForegroundHwnd(" in content and "esc-skipped-main-foreground:" in content,
            "_step() must not ESC the main dialog window — ESC == Cancel == "
            "close on a dialog-based app (PuTTY Configuration). It should only "
            "ESC a real popup/dropdown that holds the foreground (PuTTY "
            "2026-07-14: unconditional osActivate('')+ESC closed the app on "
            "every failed step)",
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
    # 2026-07-14: osScroll도 osScopedInvoke와 같은 이유로 PowerShell(managed
    # UIA)에서 Python(comtypes COM)으로 교체됨 — osScopedInvoke.py 포팅 후
    # 재검증한 실제 GUI 실행에서 osScroll.ps1의 FromHandle이 재시도 1회로도
    # 여전히 실패하는 것을 재차 확인(콜드스타트가 아니라 managed UIA 자체가
    # 이 native Win32 다이얼로그 부류에서 신뢰 안 됨 — osScopedInvoke와 동일 결론).
    scroll_py = saved_helper("osScroll.py")
    check(
        "osScroll.py scrolls via UIA ScrollPattern first",
        "ScrollPattern" in scroll_py,
        "missing ScrollPattern — scroll must be programmatic, not pixel injection",
    )
    check(
        "osScroll.py uses COM IUIAutomation (comtypes), not managed UIA",
        "import comtypes" in scroll_py and "System.Windows.Automation" not in scroll_py,
        "expected a comtypes-based COM script — managed UIA (System.Windows."
        "Automation) proved unreliable for this control class even with a "
        "retry (PuTTY 2026-07-14 re-verification)",
    )
    check(
        "osScroll.py falls back to PostMessageW (async), never SendMessageW",
        "PostMessageW" in scroll_py and "SendMessageW" not in scroll_py,
        "PoC 2026-07-10: SendMessageW (sync) crashed charmap.exe — fallback "
        "must be PostMessageW",
    )
    check(
        "osScroll.py has no physical pointer injection",
        "SetCursorPos" not in scroll_py and "mouse_event" not in scroll_py,
        "found SetCursorPos/mouse_event — coordinate signal injection is forbidden",
    )
    check(
        "osScroll.ps1 is no longer generated (replaced by .py)",
        not any(str(p).endswith("osScroll.ps1") for p in saved_paths),
        f"stale managed-UIA helper still saved: {saved_paths}",
    )
    check(
        "osClick.ps1 / osDrag.ps1 are no longer generated",
        not any(str(p).endswith(("osClick.ps1", "osDrag.ps1")) for p in saved_paths),
        f"coordinate-injection helpers still saved: {saved_paths}",
    )
    # savedPaths에 없는 것과 별개로, generate가 미리 심어둔 stale 파일을
    # 디스크에서 실제로 지웠는지 확인 (saveFiles의 OBSOLETE_FILES 정리).
    for stale in ("osClick.ps1", "osDrag.ps1", "osScopedInvoke.ps1", "osScroll.ps1", "osExpandCollapse.ps1", "wdio.conf.js"):
        check(
            f"stale {stale} removed from disk by generate",
            not os.path.exists(os.path.join(out_dir, stale)),
            f"{stale} still on disk — saveFiles obsolete-cleanup regressed",
        )
    check(
        "wdio.conf.js is not (re-)generated",
        not any(str(p).endswith("wdio.conf.js") for p in saved_paths),
        f"wdio.conf.js still saved — it's an unread legacy artifact, should not be generated: {saved_paths}",
    )
    # 2026-07-14: osScopedInvoke는 managed UIA(System.Windows.Automation)가
    # PuTTY 같은 native Win32 다이얼로그에서 Button/ComboBox 내부를 못 보는
    # 것이 실측 확정(diag_managed_uia.ps1: Button-controlType 0개)되어
    # PowerShell에서 Python(comtypes COM IUIAutomation)으로 교체됐다 —
    # agent.py/poc/poc3_dialog_e2e.py가 이미 같은 앱 부류에서 검증해둔 스택.
    check(
        "osScopedInvoke.ps1 is no longer generated (replaced by .py)",
        not any(str(p).endswith("osScopedInvoke.ps1") for p in saved_paths),
        f"stale managed-UIA helper still saved: {saved_paths}",
    )
    check(
        "osScopedInvoke.py is generated",
        any(str(p).endswith("osScopedInvoke.py") for p in saved_paths),
        f"COM-based replay helper missing from saved files: {saved_paths}",
    )
    scoped_invoke_py = saved_helper("osScopedInvoke.py")
    check(
        "osScopedInvoke.py uses COM IUIAutomation (comtypes), not managed UIA",
        "import comtypes" in scoped_invoke_py and "System.Windows.Automation" not in scoped_invoke_py,
        "expected a comtypes-based COM script — managed UIA (System.Windows."
        "Automation) can't see Button/ComboBox internals on native Win32 "
        "dialogs (PuTTY 2026-07-14 diagnosis)",
    )
    # 2026-07-17 (2차): timestamped replay diagnosis (FileZilla Site Manager)
    # found osScopedInvoke.py reporting "target not found" for clicks that
    # actually found the element every attempt (item=found) but had no
    # actionable Invoke/SelectionItem pattern — Tree containers and Edit
    # fields don't support either. A plain click's real intent there is just
    # focus, so passive control types should count SetFocus as success while
    # actionable controls (Button/MenuItem/TreeItem) still require a real
    # Invoke/Select (false-PASS guard, 2026-07-13 3rd lesson).
    check(
        "osScopedInvoke.py treats passive controls (Edit/Tree/Tab/Pane/Document) "
        "as clicked when SetFocus succeeds even without Invoke/Select",
        "PASSIVE_CONTROL_TYPES = {50004, 50030, 50033, 50018, 50023}" in scoped_invoke_py
        and "if focus_ok and ctrl_type in PASSIVE_CONTROL_TYPES:" in scoped_invoke_py,
        "expected passive-controltype fallback in invoke_item() — without it, "
        "a captured click on a Tree container or Edit field always fails even "
        "though the element is found every retry (FileZilla Site Manager 2026-07-17)",
    )
    check(
        "osScopedInvoke.py strips a trailing newline before SetValue (type path)",
        "value = text[:-1] if text.endswith('\\n') else text" in scoped_invoke_py,
        "expected trailing-newline strip in type_item() — a captured rename-box "
        "commit like 'd\\n' otherwise gets typed literally instead of pressing "
        "Enter, leaving the edit box uncommitted and blocking sibling-tab "
        "lookups in the same dialog (FileZilla Site Manager 2026-07-17)",
    )
    # osScopedInvoke.py is embedded as a JS template literal in server.js —
    # a bare \n inside that backtick string is interpreted by JS as a real
    # newline BEFORE it ever reaches the .py file, silently splitting a
    # Python string literal across two lines (unterminated string literal).
    # Caught once already while writing the fix above (the string-match check
    # only failed to match by accident; it does not prove the file parses).
    # A real syntax check is the only thing that actually guards this class
    # of JS-template-escaping bug for a generated Python file.
    try:
        compile(scoped_invoke_py, "osScopedInvoke.py", "exec")
        py_syntax_ok, py_syntax_err = True, ""
    except SyntaxError as e:
        py_syntax_ok, py_syntax_err = False, str(e)
    check(
        "osScopedInvoke.py is syntactically valid Python",
        py_syntax_ok,
        py_syntax_err or "generated helper failed to compile",
    )
    # 2026-07-14: osExpandCollapse도 같은 이유로 .NET managed UIA(.ps1)에서
    # comtypes COM UIA(.py)로 교체 — managed UIA는 레거시 SysTreeView32 TreeItem을
    # 못 봐서 "Window" 트리 노드 펼치기가 항상 "target element not found"로
    # 실패했다(poc/FINDINGS.md:118-129, PuTTY 2026-07-14 GUI STEP 11).
    check(
        "osExpandCollapse.ps1 is no longer generated (replaced by .py)",
        not any(str(p).endswith("osExpandCollapse.ps1") for p in saved_paths),
        f"stale managed-UIA helper still saved: {saved_paths}",
    )
    check(
        "osExpandCollapse.py is generated",
        any(str(p).endswith("osExpandCollapse.py") for p in saved_paths),
        f"COM-based expand/collapse helper missing from saved files: {saved_paths}",
    )
    expand_py = saved_helper("osExpandCollapse.py")
    check(
        "osExpandCollapse.py uses COM IUIAutomation (comtypes) + ExpandCollapsePattern",
        "import comtypes" in expand_py
        and "UIA_ExpandCollapsePatternId = 10005" in expand_py
        and "System.Windows.Automation" not in expand_py,
        "expected a comtypes COM ExpandCollapse script — managed UIA is blind "
        "to legacy SysTreeView32 TreeItems (PuTTY 2026-07-14, poc/FINDINGS.md)",
    )


def step_wdio_generate_app_state_reset():
    print("\n[8b] App-state reset ported from removed wdio.conf.js onWorkerStart hook")
    # wdio.conf.js's onWorkerStart hook used to clear 7-Zip's registry-
    # persisted last-visited folder (KNOWN_APP_STATE_RESET) before each run.
    # Now that wdio.conf.js is no longer generated, that reset must instead
    # be spliced directly into the standalone script's run() function —
    # verify it survived the move instead of being silently dropped.
    request("DELETE", "/api/events")
    for ev in MOCK_EVENTS:
        request("POST", "/api/events", ev)
    status, body = request("POST", "/api/generate", {
        "appName": "SevenZipStateReset",
        "exePath": "C:\\Program Files\\7-Zip\\7zFM.exe",
        "platform": PLATFORM,
    }, timeout=30)
    check("POST /api/generate (7zFM state-reset) returns 200", status == 200, f"got {status}")
    if status != 200:
        check("(skipped state-reset checks)", False, body.get("message", ""))
        return
    files = body.get("files", [])
    for f in files:
        content = f.get("content", "")
        check(
            f"  {f.get('filename')} ports the 7zFM.exe registry-reset into run()",
            "PanelPath0" not in content  # the raw command is base64-encoded, not literal
            and "-EncodedCommand" in content
            and "[state-reset]" in content,
            "expected an -EncodedCommand execSync call logging '[state-reset]' "
            "near the top of run() — the app-state-reset feature that used to "
            "live in wdio.conf.js's onWorkerStart hook must not be silently "
            "dropped now that wdio.conf.js itself is no longer generated",
        )


def step_wdio_generate_anim_settle():
    print("\n[8c] session_meta launch-animation rect must not misclassify "
          "same-window clicks as cross-window")
    request("DELETE", "/api/events")
    request("POST", "/api/events", ANIM_SESSION_META)
    for ev in ANIM_EVENTS:
        request("POST", "/api/events", ev)
    status, body = request("POST", "/api/generate", {
        "appName": ANIM_APP,
        "platform": PLATFORM,
    }, timeout=30)
    check("POST /api/generate (anim-settle) returns 200", status == 200, f"got {status}")
    if status != 200:
        check("(skipped anim-settle checks)", False, body.get("message", ""))
        return
    files = body.get("files", [])
    for f in files:
        content = f.get("content", "")
        check(
            f"  {f.get('filename')} does not misclassify same-window clicks as (cross-window)",
            "(cross-window)" not in content,
            "recordedRect must prefer an actual click event's settled window "
            "rect over session_meta.initialWindow (which can be captured "
            "mid-launch-animation, before the window reaches the resting "
            "geometry every click actually sees) — otherwise every click in "
            "a plain single-window recording gets routed through the slower "
            "COM osScopedInvoke path instead of a plain browser click",
        )


def step_wdio_generate_nested_dropdown():
    print("\n[8d] Nested-dialog DropDown trigger must drop its captured "
          "state-dependent Name even when NOT merged with a following item "
          "(2026-07-21, real 7-Zip Options 'Language:' combo)")
    request("DELETE", "/api/events")
    request("POST", "/api/events", {
        "action": "session_meta", "app": NESTED_DROPDOWN_APP, "platform": PLATFORM,
        "timestamp": time.time(), "isElectron": False,
        "initialWindow": {"left": 0, "top": 0, "width": 800, "height": 600},
    })
    for ev in NESTED_DROPDOWN_EVENTS:
        request("POST", "/api/events", ev)
    status, body = request("POST", "/api/generate", {
        "appName": NESTED_DROPDOWN_APP,
        "platform": PLATFORM,
    }, timeout=30)
    check("POST /api/generate (nested-dropdown) returns 200", status == 200, f"got {status}")
    if status != 200:
        check("(skipped nested-dropdown checks)", False, body.get("message", ""))
        return
    files = body.get("files", [])
    for f in files:
        content = f.get("content", "")
        check(
            f"  {f.get('filename')} drops the DropDown trigger's state-dependent "
            "Name even when unmerged",
            '"automationId":"DropDown"' in content.replace(" ", "")
            and '"name":"닫기"' not in content,
            "expected the standalone (non-merged) cross-window click's target "
            "to have automationId='DropDown' with an EMPTY name — the captured "
            "Name ('닫기'/Close) only reflects the list's already-open state and "
            "never matches at replay start (closed, real name '열기'/Open), so "
            "an AND condition on both fields matches nothing (PuTTY 2026-07-14 "
            "class of bug, reappearing here because this click falls through "
            "to the unmerged branch instead of the triggerTarget-only fix path)",
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
        # 2026-07-17: owned-다이얼로그 COM 라우팅 — Root-세션 REST 폴백이
        # 매치 여부와 무관하게 매번 15~20초 고정 비용이 드는 것을 실측
        # 확정(FileZilla Site Manager 진단, 빈 결과조차 15.6초). 코드생성
        # 스텝은 이제 getWindowSession()을 직접 부르지 않고
        # _typeScopedOrCom()을 통해 owned 여부를 런타임에 판단한다.
        check(
            f"  {fname} routes session-mode typing through _typeScopedOrCom "
            "(owned-dialog COM fast path)",
            "_typeScopedOrCom(" in content,
            "generated type step must call _typeScopedOrCom(title, selector, "
            "value) instead of manually resolving getWindowSession()+"
            "_typeScoped() inline — without this, owned dialogs always pay "
            "the 15-20s Root-scan REST fallback even though the hwnd is "
            "already known from EnumWindows",
        )
        check(
            f"  {fname} defines osScopedType (COM typing for owned dialogs)",
            "function osScopedType(hwnd, target, text)" in content
            and "--text-b64" in content,
            "missing osScopedType()/--text-b64 wiring — owned-dialog typing "
            "has no COM fallback and must fall through to the slow REST path",
        )
        check(
            f"  {fname} defines _parseSelectorToTarget (XPath -> COM condition)",
            "function _parseSelectorToTarget(selector)" in content,
            "missing the selector translator — _clickScoped/_typeScopedOrCom "
            "can't route simple ~id / @AutomationId / @Name selectors to the "
            "COM path without it",
        )
        check(
            f"  {fname} getWindowSession short-circuits owned windows instead "
            "of Root-scanning",
            "owned: true" in content,
            "getWindowSession() must return { owned: true, hwnd } immediately "
            "when a window is owned, instead of falling through to the "
            "Root-session REST XPath scan (empirically ~15-20s per call "
            "regardless of match, 2026-07-17 FileZilla diagnosis)",
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
            f"  {fname} asserts on _failures via process.exitCode (no Jasmine expect)",
            "process.exitCode = 1" in content and "expect(_failures)" not in content,
            "missing the standalone pass/fail exit-code check, or a leftover "
            "Jasmine expect() that would crash under plain `node`",
        )
        check(
            f"  {fname} is a standalone script (no describe/it/browser.*)",
            "describe(" not in content and "browser." not in content and "async function run()" in content,
            "session-mode file must also run under plain `node <file>.js` — "
            "no Jasmine wrapper, no WDIO `browser` global "
            "(2026-07-17: setup-dependency gap)",
        )
        check(
            f"  {fname} self-starts Appium and opens its own Root session",
            "async function ensureAppium()" in content and "_createSession('Root')" in content,
            "session-mode file must start/reuse Appium itself and open the "
            "Root session it used to get for free via WDIO's injected "
            "`browser`",
        )
        # 2026-07-16 multi-window segmenting fix: an explicit, separately
        # logged "switch to window" step must appear at every hwnd boundary
        # (Hamza review feedback — window1/window2 actions must visibly be
        # grouped, not just implicitly work via getWindowSession()).
        check(
            f"  {fname} has a _switchWindow() helper (evicts stale title-keyed cache)",
            "async function _switchWindow(" in content,
            "missing _switchWindow — general getWindowSession(title) path has "
            "no defense against reusing a dead session/hwnd for a revisited "
            "same-titled window (2026-07-15 'bug 2', general path unpatched)",
        )
        switch_count = content.count("await _switchWindow('")
        check(
            f"  {fname} emits a switch-to-window step at each of the 3 hwnd boundaries "
            "(A1B2 -> C3D4 -> A1B2 revisit)",
            switch_count == 3,
            f"expected 3 '_switchWindow(' calls (initial + dialog-open + "
            f"revisit-main), got {switch_count}",
        )
        # 2026-07-17 multi-window code-structure feedback: elements belonging
        # to a new screen must be visibly grouped under it IN THE GENERATED
        # CODE (not just an implicit runtime switch) — a window legend up
        # top and a [Wn] section banner at every hwnd boundary (3 for
        # MockMulti's A1B2 -> C3D4 -> A1B2 revisit), independent of whether
        # that boundary happens to also emit a runtime _switchWindow() call.
        check(
            f"  {fname} has a window legend listing all 3 segments",
            "// Windows in this recording:" in content
            and "[W1]" in content and "[W2]" in content and "[W3]" in content,
            "missing the window legend / [Wn] labels — multi-window code "
            "structure must be visible without reading replay logs",
        )
        check(
            f"  {fname} banners each window section in both the page-object class and the test body",
            content.count("[W1]") >= 2 and content.count("[W2]") >= 2 and content.count("[W3]") >= 2,
            "expected each [Wn] label to appear at least twice per window "
            "(once in the legend/class banner, once again at the matching "
            "test-body step) — got page-object and test-body banners out of "
            "sync",
        )
        check(
            f"  {fname} labels the switch step visibly in the step list",
            "_step('switch to window:" in content,
            "switch step isn't wrapped in _step() with a visible label — "
            "window1/window2 grouping won't show up in the replay log",
        )
        check(
            f"  {fname} replays expandCollapse via osExpandCollapse() even in session mode",
            "osExpandCollapse(_hwndCache[_mainTitleFrag]" in content,
            "session-mode expandCollapse events must not be silently skipped — "
            "FileZilla-style File-menu navigation never actually selected the "
            "target menu item in session mode (2026-07-16, root cause of the "
            "Site Manager dialog never opening during replay)",
        )
        check(
            f"  {fname} actually DEFINES osExpandCollapse() (not just calls it)",
            "function osExpandCollapse(hwnd, target, itemName)" in content,
            "SESSION_HEADER never defined this helper — calling it threw "
            "'osExpandCollapse is not defined' at replay time even after the "
            "call-site gate was fixed (2026-07-16, caught on real FileZilla "
            "GUI run — the call-site check above alone didn't catch this)",
        )
        check(
            f"  {fname} merges the File-menu trigger+item into one osExpandCollapse call",
            "Site Manager" in content,
            "expected the merged item name 'Site Manager' to appear as the "
            "itemName argument to osExpandCollapse()",
        )


def step_wdio_generate_window_collision():
    print("\n[9b] Multi-window title-collision — same literal title, different hwnd")
    request("DELETE", "/api/events")
    for ev in COLLISION_EVENTS:
        request("POST", "/api/events", ev)

    status, body = request("POST", "/api/generate", {
        "appName": COLLISION_APP,
        "exePath": COLLISION_EXE,
        "platform": PLATFORM,
    }, timeout=30)
    check("POST /api/generate (collision) returns 200", status == 200, f"got {status}")
    if status != 200:
        check("(skipped collision checks)", False, body.get("message", ""))
        return
    files = body.get("files", [])
    for f in files:
        fname = f.get("filename", "")
        content = f.get("content", "")
        # All 3 events share the literal title "7-Zip" but cross hwnd
        # boundaries E1E1 -> F2F2 -> E1E1 — a naive "already switched to
        # this title" cache would collapse this to 1 switch (or 0 after the
        # first), silently reusing the dead dialog session/hwnd for the
        # revisit (exactly the real 7-Zip STEP 6+ click-not-found bug,
        # 2026-07-15). Must still fire on every hwnd change.
        switch_count = content.count("await _switchWindow('7-Zip')")
        check(
            f"  {fname} switches window 3 times despite identical title text "
            "(E1E1 -> F2F2 -> E1E1)",
            switch_count == 3,
            f"expected 3 '_switchWindow('7-Zip')' calls (title collision must "
            f"not suppress hwnd-boundary detection), got {switch_count}",
        )


def step_wdio_generate_delayed_hwnd():
    print("\n[9d] Delayed rootHwndHex — windowTitle arrives before hwnd tagging "
          "(2026-07-17 FileZilla GUI finding)")
    request("DELETE", "/api/events")
    for ev in DELAYED_HWND_EVENTS:
        request("POST", "/api/events", ev)

    status, body = request("POST", "/api/generate", {
        "appName": DELAYED_HWND_APP,
        "exePath": DELAYED_HWND_EXE,
        "platform": PLATFORM,
    }, timeout=30)
    check("POST /api/generate (delayed-hwnd) returns 200", status == 200, f"got {status}")
    if status != 200:
        check("(skipped delayed-hwnd checks)", False, body.get("message", ""))
        return
    files = body.get("files", [])
    for f in files:
        fname = f.get("filename", "")
        content = f.get("content", "")
        check(
            f"  {fname} emits switch to window: Dialog despite rootHwndHex "
            "being absent on the first events inside it",
            "switch to window: Dialog" in content,
            "windowTitle flips to 'Dialog' immediately but rootHwndHex stays "
            "empty for 2 events (PID self-heal lets the click through before "
            "the watcher formally registers the hwnd) — boundary detection "
            "keyed only on rootHwndHex misses this transition entirely, so "
            "no _switchWindow() ever fires for the dialog (real bug: FileZilla "
            "Site Manager typing/clicks silently used a stale/wrong session)",
        )
        check(
            f"  {fname} labels the Dialog window's own section, not the "
            "previous window's",
            '[W2] Dialog' in content,
            "the [Wn] banner must attribute Field1/Field2 (windowTitle="
            "'Dialog', rootHwndHex=None) to the Dialog's own section — a "
            "hwnd-only boundary check leaves them mislabeled under [W1] "
            "Main Window",
        )
        # The banner for W2 should appear before Field1's click, not only at
        # OkButton (index 4, the first event with a real rootHwndHex) — i.e.
        # the window section must start at the FIRST Dialog event.
        w1_pos = content.find('[W1] Main Window')
        w2_pos = content.find('[W2] Dialog')
        field1_pos = content.find("'2:click Field1'")
        check(
            f"  {fname} starts the [W2] Dialog section before Field1's step, "
            "not after it",
            -1 not in (w1_pos, w2_pos, field1_pos) and w1_pos < w2_pos < field1_pos,
            f"positions: [W1]={w1_pos} [W2]={w2_pos} Field1 step={field1_pos} — "
            "expected [W2] to appear right before Field1, proving the boundary "
            "was detected at the FIRST dialog event, not delayed until "
            "OkButton where rootHwndHex finally shows up",
        )


def step_wdio_generate_expand_redundant_trigger():
    print("\n[9c] Redundant ComboBox trigger re-clicks before the real item "
          "(2026-07-17 FileZilla GUI finding)")
    request("DELETE", "/api/events")
    for ev in EXPAND_REDUNDANT_EVENTS:
        request("POST", "/api/events", ev)

    status, body = request("POST", "/api/generate", {
        "appName": EXPAND_REDUNDANT_APP,
        "platform": PLATFORM,
    }, timeout=30)
    check("POST /api/generate (expand-redundant) returns 200", status == 200, f"got {status}")
    if status != 200:
        check("(skipped expand-redundant checks)", False, body.get("message", ""))
        return
    files = body.get("files", [])
    for f in files:
        fname = f.get("filename", "")
        content = f.get("content", "")
        if "ById" not in fname:
            continue
        check(
            f"  {fname} merges the 3 redundant trigger re-clicks with the REAL item (not itself)",
            'osExpandCollapse(_appHwnd, {"automationId":"5999","className":"ComboBox","name":"Combo"}, "Red")' in content,
            "expected the 3 consecutive re-clicks of the same ComboBox trigger "
            "to collapse into ONE osExpandCollapse call whose itemName is the "
            "real item ('Red') that came after them — real FileZilla capture "
            "had 3 physical clicks on '배경색(B):' before it opened, and the "
            "old merge logic paired click #1 with click #2 (also just the "
            "trigger) instead of skipping ahead to the real item",
        )
        check(
            f"  {fname} never merges the trigger with itself (self-referencing itemName)",
            'osExpandCollapse(_appHwnd, {"automationId":"5999","className":"ComboBox","name":"Combo"}, "Combo")' not in content,
            "found a self-referencing merge — itemName equals the trigger's "
            "own name, which is exactly the STEP6 bug seen in the real "
            "FileZilla run ('배경색(B): -> 배경색(B):')",
        )
        expand_step_count = content.count("_step('")
        check(
            f"  {fname} emits exactly 2 steps (1 collapsed ComboBox merge + 1 normal MenuItem merge)",
            expand_step_count == 2,
            f"got {expand_step_count} — the 3 redundant trigger clicks + 1 item "
            "should collapse to 1 step, plus the unrelated File->Open "
            "MenuItem merge = 2 total (not 4, which would mean the redundant "
            "re-clicks leaked out as their own broken steps)",
        )
        check(
            f"  {fname} still correctly merges an ordinary MenuItem trigger+item pair (regression)",
            'osExpandCollapse(_appHwnd, {"automationId":"","className":"MenuItem","name":"File"}, "Open")' in content,
            "the fix must not disturb the existing non-redundant merge path",
        )


def step_wdio_generate_native():
    print("\n[10] Native Win32 dialog generation — numeric AutomationId handling")
    request("DELETE", "/api/events")
    request("POST", "/api/events", NATIVE_SESSION_META)
    for ev in NATIVE_EVENTS:
        request("POST", "/api/events", ev)

    status, body = request("POST", "/api/generate", {
        "appName": NATIVE_APP,
        "platform": PLATFORM,
    }, timeout=30)
    check("POST /api/generate (native) returns 200", status == 200, f"got {status}")
    if status != 200:
        check("(skipped native checks)", False, body.get("message", ""))
        return
    files = body.get("files", [])
    for f in files:
        fname = f.get("filename", "")
        content = f.get("content", "")
        if "ById" not in fname:
            continue
        check(
            f"  {fname} trusts a numeric AutomationId on a Button/CheckBox",
            "'~1049'" in content,
            "stable Win32 resource ID (1049) was rejected as if it were a "
            "ListView slot index — breaks AutomationId-based XPath on "
            "native dialogs (PuTTY 2026-07-13)",
        )
        check(
            f"  {fname} still rejects a numeric AutomationId on a TreeItem",
            "'~6'" not in content and 'Name="Selection"' in content,
            "runtime slot index (6) on a virtualized TreeItem was trusted as "
            "a stable id — will drift as the tree scrolls/reorders",
        )
        # Reused numeric AutomationId across different fields (2026-07-17,
        # FileZilla Site Manager: automationId "5999" on ~12 Edit fields).
        check(
            f"  {fname} ANDs the Name into a reused numeric AutomationId (Host field)",
            '//Edit[@AutomationId="5999" and @Name="Host:"]' in content,
            "a bare '~5999' selector matches whichever field WinAppDriver "
            "finds first — the generated click/type step for the Host field "
            "must combine automationId+Name to disambiguate it from the "
            "other 11 fields sharing the same id (FileZilla 2026-07-17)",
        )
        check(
            f"  {fname} ANDs the Name into a reused numeric AutomationId (Port field)",
            '//Edit[@AutomationId="5999" and @Name="Port:"]' in content,
            "same disambiguation must apply independently to every field "
            "sharing the reused id, not just the first one encountered",
        )
        check(
            f"  {fname} never emits the ambiguous bare '~5999' for the reused id",
            "'~5999'" not in content,
            "if the bare accessibility-id selector survives anywhere, that "
            "step still resolves to the wrong field at replay time",
        )
        check(
            f"  {fname} still emits the bare '~1049' for a NON-reused numeric id (regression)",
            "'~1049'" in content,
            "the reuse-detection must not over-trigger on a numeric id that "
            "only appears once — that would needlessly lengthen a selector "
            "that was already unambiguous",
        )
        # ExpandCollapsePattern replay (2026-07-13, poc/diag_expandcollapse.py):
        # ComboBox open+select must merge into ONE osExpandCollapse() call
        # with the item name; a standalone TreeItem +/- toggle must call it
        # with itemName=null and must NOT swallow the click that follows it.
        check(
            f"  {fname} merges ComboBox open+select into one osExpandCollapse() call",
            'osExpandCollapse(_appHwnd, {"automationId":"1044"' in content
            and '"SOCKS 5"' in content,
            "ComboBox click + item click were not merged into a single "
            "osExpandCollapse() step — dropdown item is unreachable via a "
            "plain click() (PuTTY 2026-07-13)",
        )
        check(
            f"  {fname} replays a standalone TreeItem toggle with itemName=null",
            'osExpandCollapse(_appHwnd, {"automationId":"","className":"TreeItem","name":"Window"}, null)' in content,
            "TreeItem +/- toggle must call osExpandCollapse() with no item "
            "name (pure expand/collapse, not an item-selection gesture)",
        )
        check(
            f"  {fname} still replays the click that follows a TreeItem toggle separately",
            'Name="Data"' in content,
            "the TreeItem toggle's expand-merge must not swallow the "
            "unrelated click that happens to follow it",
        )
        # Cross-window click (2026-07-13, PuTTY "Remote character set:"
        # follow-up): a click whose own captured window geometry matches the
        # main window stays a plain click() elsewhere in this scenario
        # (Data/Colours/etc TreeItems above already cover that). A trigger
        # click (main window) immediately followed by a click in a DIFFERENT
        # window (e.g. a "DropDown" button opening a popup list) must be
        # MERGED into a single osScopedInvoke() call carrying both the item
        # and the trigger — splitting them into two separate steps/processes
        # was found to race the popup auto-closing before the item search
        # ran (PuTTY 'Remote character set:', 2026-07-13).
        check(
            f"  {fname} merges a same-window trigger + cross-window item into one osScopedInvoke() call",
            'osScopedInvoke(_appHwnd, {"automationId":"","className":"","name":"Some Encoding"}, '
            '{"automationId":"DropDown","className":"","name":""})' in content,
            "trigger click (DropDown button) and the cross-window item click "
            "must merge into one osScopedInvoke(item, trigger) call instead "
            "of two separate steps — splitting them races the popup "
            "auto-closing before the item search runs (PuTTY 2026-07-13)",
        )
        # 2026-07-14 regression: the trigger's captured Name ("close") must
        # NEVER survive into the generated selector when automationId is
        # present — trusting it made osScopedInvoke's AND-condition match
        # zero elements at replay time (control starts closed, not "close"),
        # so the trigger was silently never invoked and the dropdown never
        # opened (PuTTY Translation "Remote character set:", 2026-07-14).
        check(
            f"  {fname} drops the trigger's state-dependent Name when automationId is present",
            '"name":"close"' not in content,
            "trigger selector still carries the captured Name — a state-"
            "dependent label (e.g. a ComboBox dropdown button's open/close "
            "accessible name) baked into the AND-condition never matches at "
            "replay time, so the trigger silently fails to invoke and the "
            "dropdown never opens (PuTTY 2026-07-14)",
        )
        check(
            f"  {fname} does not emit a separate step for the merged-away trigger click",
            "browser.$('~DropDown')" not in content,
            "the trigger click should be consumed into the merged "
            "osScopedInvoke() call, not also replayed as its own step",
        )
        # Merge-across-scroll (2026-07-14): a main-window trigger + intervening
        # scroll + cross-window item must merge into ONE osScopedInvoke(item,
        # trigger) with the scroll DROPPED. If the merge misses, the trigger is
        # left as a standalone click (titlebar-X hazard in ByClass) and the
        # scroll renders as its own osScrollEl() step (PuTTY 2026-07-14).
        check(
            f"  {fname} merges trigger+scroll+item into one osScopedInvoke() and drops the scroll",
            'osScopedInvoke(_appHwnd, {"automationId":"","className":"","name":"UTF-8 Item"}, '
            '{"automationId":"DropDown","className":"","name":""})' in content
            and "osScrollEl(_appHwnd," not in content,  # call site, not the header's function def
            "trigger click + intervening scroll + cross-window item must merge "
            "into one osScopedInvoke(item, trigger); the scroll must be dropped "
            "(COM FindFirst finds the item regardless of scroll position) — "
            "otherwise the standalone trigger closes the app in ByClass "
            "(titlebar X) and the scroll runs against a stale window (PuTTY "
            "2026-07-14)",
        )
        step_count = content.count("_step('")
        # NATIVE_EVENTS: 14 events -> CheckBox(1) + TreeItem-Selection(1) +
        # ComboBox+SOCKS5 merged(1) + TreeItem-Window-toggle(1) + Data(1) +
        # DropDown+cross-window-item merged(1) + DropDown+scroll+item merged(1)
        # + Host-click(1) + Port-click(1) + Host-type(1) = 10.
        check(
            f"  {fname} step count (14 events -> 10 steps: 3 merges, scroll dropped)",
            step_count == 10,
            f"got {step_count} _step(...) invocations",
        )

    # DropDown selector guard (2026-07-14, defense-in-depth for the ByClass
    # path): a ComboBox DropDown arrow (automationId="DropDown", name="close")
    # must NEVER resolve to //Button[@Name="close"] — in Korean Windows that
    # Name also belongs to the titlebar Close (X) button, so clicking it closes
    # the app. In this scenario every DropDown is merged away, so the hazardous
    # selector must be entirely absent from the ByClass output.
    for f in files:
        fname = f.get("filename", "")
        content = f.get("content", "")
        if "ByClass" not in fname:
            continue
        check(
            f"  {fname} never emits a titlebar-risk //Button[@Name=\"close\"] selector",
            '//Button[@Name="close"]' not in content,
            "a DropDown arrow leaked into a bare Name-based Button selector — "
            "matches the titlebar Close (X) button and closes the app "
            "(PuTTY ByClass 2026-07-14). Use ~DropDown / merge it away.",
        )
        # wdioSelectorByClass was NOT touched by the reused-id fix (it already
        # ANDs ClassName+Name unconditionally) — confirm it still resolves the
        # reused-id Host/Port fields correctly, i.e. no regression there.
        check(
            f"  {fname} already disambiguates the reused-id fields via ClassName+Name (regression)",
            '//Edit[@ClassName="Edit" and @Name="Host:"]' in content
            and '//Edit[@ClassName="Edit" and @Name="Port:"]' in content,
            "wdioSelectorByClass's existing ClassName+Name combo must keep "
            "working unchanged after the ById-side fix",
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
    step_wdio_generate_app_state_reset()
    step_wdio_generate_anim_settle()
    step_wdio_generate_nested_dropdown()
    step_wdio_generate_session()
    step_wdio_generate_window_collision()
    step_wdio_generate_delayed_hwnd()
    step_wdio_generate_expand_redundant_trigger()
    step_wdio_generate_native()

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
