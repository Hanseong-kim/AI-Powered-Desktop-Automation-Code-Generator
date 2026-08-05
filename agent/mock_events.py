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
import py_compile
import sys
import time

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
import urllib.error
import urllib.request

BASE = "http://localhost:3002"
# NOTE: deliberately NOT "Calculator". The regression gate calls /api/generate
# without an exePath, so whatever folder it writes to gets overwritten with a
# synthetic, exePath-less build. Using the real preset's name meant a routine
# `python agent/mock_events.py` run silently clobbered generated-wdio/Calculator
# (real capture -> `Capability: app cannot be empty` at replay time, diagnosed
# 2026-07-24). Every scenario in this file must own an output folder that no
# real recording preset uses, and that folder must be gitignored.
APP_NAME = "MockCalculator"
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


import re

# 2026-07-27: generalized regression gate for the "call site added, definition
# never followed" class of bug (osExpandCollapse 2026-07-16 "Bug D",
# osForegroundHwnd 2026-07-27 — both times a helper got called from one
# header/branch but only ever defined in the other). Rather than adding one
# more one-off symbol check each time this recurs, verify EVERY self-defined
# helper the generated file calls (os*/_* names) actually has a definition
# somewhere in the same file.
# 2026-07-27: the 9 os*.ps1/os*.py scripts a generate writes purely for human
# inspection — no longer read by the generated .js itself (it embeds them),
# so they belong in generated-wdio/_debug-helpers/<App>/, not the app's own
# output folder. Shared by both the simple- and session-mode generate checks.
DEBUG_HELPER_NAMES = (
    "osScroll.py", "osWindowRect.ps1", "osMoveWindow.ps1", "osType.ps1",
    "osActivate.ps1", "osDismissPopup.ps1", "osEscape.ps1",
    "osExpandCollapse.py", "osScopedInvoke.py",
)

_CALL_SITE_RE = re.compile(r"(?<![.\w$])((?:os[A-Z]\w*)|(?:_[A-Za-z]\w*))\s*\(")
_DEF_RE = re.compile(r"(?:function\s+([A-Za-z_]\w*)\s*\(|(?:const|let)\s+([A-Za-z_]\w*)\s*=)")
# 2026-07-27: the .js now embeds the os*.ps1/os*.py helper scripts verbatim
# as JSON-stringified JS string literals (const _H = {...}) so the file is
# copy-portable without its sibling scripts. That embedded PowerShell/Python
# source text isn't JS — scanning it for JS call-site/definition patterns
# produces false positives (e.g. a Python `def _enable_per_monitor_dpi_awareness():`
# looks like an undefined JS call). Strip that one block before any textual
# JS-content check.
_EMBEDDED_HELPERS_RE = re.compile(r"const _H = \{[\s\S]*?\n\};\n")


def _strip_embedded_helpers(content):
    return _EMBEDDED_HELPERS_RE.sub("", content)


def check_helpers_defined(fname, content):
    content = _strip_embedded_helpers(content)
    called = set(_CALL_SITE_RE.findall(content))
    defined = set()
    for a, b in _DEF_RE.findall(content):
        defined.add(a or b)
    missing = sorted(n for n in called if n not in defined)
    check(
        f"  {fname} has no undefined helper call sites",
        not missing,
        f"called but never defined in this file: {missing}" if missing else "",
    )


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

# Delphi/VCL hwnd-as-AutomationId scenario (2026-07-29, HeidiSQL GUI failure
# follow-up, confirmed by live COM UIA diagnostic poc/diag_heidisql_ids.py:
# 13 of 19 automationIds in HeidiSQL's session-manager window were exactly
# equal to the control's own NativeWindowHandle). This id is reassigned every
# launch, so a bare '~<id>' selector can never match at replay time — and,
# left untreated, it also OUTRANKS the stable ClassName fallback beneath it
# in the selector chain. isWindowHandleId() must reject only this narrow
# case: numeric automationId == element.hwnd. A same-scenario Button whose
# numeric id does NOT equal its hwnd (the PuTTY/7-Zip/FileZilla pattern,
# already covered by NATIVE_EVENTS under a different app name) must keep
# resolving to the bare '~id' form — proving the new guard doesn't widen
# into rejecting ordinary stable Win32 resource ids.
VCL_APP = "MockVclHwndId"
VCL_EVENTS = [
    make_event("click", name="", automation_id="1051972",
               class_name="TVirtualStringTree", control_type="Pane",
               window_title="VCL Dialog", app_name=VCL_APP, x=300, y=300, index=1),
    make_event("click", name="Proxy", automation_id="1049",
               class_name="Button", control_type="Button",
               window_title="VCL Dialog", app_name=VCL_APP, x=500, y=300, index=2),
]
VCL_EVENTS[0]["element"]["hwnd"] = 1051972    # equals its own automationId -> reject
VCL_EVENTS[1]["element"]["hwnd"] = 987654     # differs from automationId 1049 -> keep
VCL_SESSION_META = {
    "action": "session_meta",
    "app": VCL_APP,
    "platform": PLATFORM,
    "timestamp": time.time(),
    "isElectron": False,
    "initialWindow": {"left": 100, "top": 100, "width": 600, "height": 400},
}

# Embedded-Chromium (WebView2) selector policy scenario (2026-08-03,
# TeamViewer 15.79). Web frameworks emit AutomationIds that are render
# counters — "TextField56", "button-461", "connectButton-498" — which change
# between builds of the app, so a selector built from one silently stops
# matching after an update. They must be rejected, but ONLY inside web
# content: WinForms designer ids ("button1", "textBox1") have exactly the
# same shape and are perfectly stable, and WinForms is a required framework.
# The discriminator is element.isWebContent, set by agent.py when the
# element's owning window has an embedded-Chromium child.
WEB_APP = "MockWebContent"
WEB_EVENTS = [
    make_event("click", name="세션 참가", automation_id="connectButton-498",
               class_name="", control_type="Button",
               window_title="TeamViewer", app_name=WEB_APP, x=300, y=300, index=1),
    make_event("click", name="", automation_id="TextField56",
               class_name="", control_type="Edit",
               window_title="TeamViewer", app_name=WEB_APP, x=320, y=340, index=2),
    make_event("click", name="Save", automation_id="button1",
               class_name="Button", control_type="Button",
               window_title="TeamViewer", app_name=WEB_APP, x=360, y=380, index=3),
]
WEB_EVENTS[0]["element"]["isWebContent"] = True
WEB_EVENTS[1]["element"]["isWebContent"] = True
WEB_EVENTS[2]["element"]["isWebContent"] = False   # native control in the same app
WEB_SESSION_META = {
    "action": "session_meta",
    "app": WEB_APP,
    "platform": PLATFORM,
    "timestamp": time.time(),
    "isElectron": False,
    "initialWindow": {"left": 100, "top": 100, "width": 1000, "height": 800},
}


def step_wdio_generate_web_content():
    print("\n[14] Embedded-Chromium render-counter AutomationId rejection")
    request("DELETE", "/api/events")
    request("POST", "/api/events", WEB_SESSION_META)
    for ev in WEB_EVENTS:
        request("POST", "/api/events", ev)
    status, body = request("POST", "/api/generate", {
        "appName": WEB_APP,
        "platform": PLATFORM,
    }, timeout=30)
    check("POST /api/generate (web content) returns 200", status == 200, f"got {status}")
    if status != 200:
        check("(skipped web-content checks)", False, body.get("message", ""))
        return
    for f in body.get("files", []):
        fname, content = f.get("filename", ""), f.get("content", "")
        check(
            f"  {fname} rejects a render-counter id inside web content",
            "connectButton-498" not in content,
            "web-framework ids are render counters and change between builds "
            "of the app, so a selector built from one stops matching after an "
            "update (TeamViewer 15.79, 2026-08-03)",
        )
        check(
            f"  {fname} falls back to the Name for that element",
            "세션 참가" in content,
            "Name is the only durable field left once the counter id is "
            "rejected — dropping both leaves no selector at all",
        )
        # wdioSelectorByClass prefers a ClassName+Name combo over a bare
        # automationId whenever the ClassName is stable ("Button" qualifies),
        # so the id itself ("button1") only ever shows up in the ById file.
        # Either form proves the same thing: a WinForms-shaped id was NOT
        # treated as a render counter and rejected.
        not_rejected = (
            "button1" in content
            or '@ClassName="Button" and @Name="Save"' in content
        )
        check(
            f"  {fname} keeps a WinForms-shaped id when NOT web content",
            not_rejected,
            "button1/textBox1 are WinForms designer names — same shape as a "
            "render counter but perfectly stable. Rejecting them would break "
            "a required framework, which is why the rule is scoped to "
            "element.isWebContent",
        )
        # 2026-08-04 (TeamViewer WebView2 replay routing fix): a live replay
        # of TeamViewer showed every single click on a web-content button
        # ("비밀번호를 복사하세요", "세션 참가", ...) silently no-op through
        # WAD's element/click — `[getCenter-diag] UIA-exposed rows (0 total)`
        # on every step, because WAD attaching to the HOST window (it does)
        # does not mean its managed UIA client can see DOM-hosted elements
        # (it can't). isWebContent elements must route through the same COM
        # stack (osScopedInvoke) agent.py's capture already uses to see them.
        check(
            f"  {fname} routes a web-content click through osScopedInvoke (COM), not WAD element/click",
            'osScopedInvoke(_appHwnd, {"automationId":"","className":"","name":"세션 참가"})' in content,
            "WAD successfully creates a scoped session for the WebView2 host "
            "window, but its managed UIA client reports zero exposed rows for "
            "everything inside it — every click through element/click is a "
            "silent no-op (measured live, TeamViewer 2026-08-04: 비밀번호를 "
            "복사하세요/세션 참가 등 핵심 동작이 전부 무반응)",
        )
        check(
            f"  {fname} does NOT route a native (non-web-content) click through osScopedInvoke",
            "_clickBySid(_appSid, null, '~button1')" in content
            or '_clickBySid(_appSid, null, \'//Button[@ClassName="Button" and @Name="Save"]\')' in content,
            "native controls in the same app window (e.g. TeamViewer's "
            "native '빠른 연결 허용' dialog) are reachable via WAD just fine — "
            "forcing them through COM too would be an unnecessary, unproven "
            "widening of the WAD-primary/COM-narrow-exception boundary",
        )


# doubleClick-on-a-native-list-row scenario (2026-08-04, 7-Zip 재생 실패 2회
# 연속 동일 재현). server.js의 ListItem 분기는 "doubleClick도 Invoke() 1회면
# 폴더 진입까지 완료"라는 2026-07-15 실측을 근거로 더블클릭을 단일 호출로
# 내보낸다. 그런데 2026-07-24에 osScopedInvoke.py의 invoke_item() 맨 앞에
# send_input_click()이 삽입되면서 `if send_input_click(...): return True`가
# 되어 **Invoke()는 도달 불가능한 죽은 코드**가 됐고, send_input_click()은
# down/up을 한 번만 보낸다. 네이티브 리스트에서 단일 클릭은 선택일 뿐 열기가
# 아니므로, 폴더가 열리지 않고 이후 스텝이 전부 무너진다(실측: 3:doubleClick
# 컴퓨터가 "invoked" 성공 보고 → 4:doubleClick C:가 target not found).
#
# 이 게이트는 수정 방식을 특정하지 않는다 — doubleClick 스텝이 만들어내는
# 호출이 동일 조건의 click 스텝과 **구별되기만** 하면 된다. 라벨 문자열만
# 다르고 실제 호출이 같으면(현재 상태) 실패한다.
DBLROW_APP = "MockDoubleClickRow"
DBLROW_EVENTS = [
    make_event("click", name="RowA", control_type="ListItem",
               window_title="Rows", app_name=DBLROW_APP, x=100, y=100, index=1),
    # 좌표를 200px 떨어뜨려 dedupeDoubleClicks()가 앞의 click을 더블클릭
    # 구성요소로 병합하지 않게 한다(DEDUPE_RADIUS=6px).
    make_event("doubleClick", name="RowB", control_type="ListItem",
               window_title="Rows", app_name=DBLROW_APP, x=100, y=300, index=2),
]
DBLROW_SESSION_META = {
    "action": "session_meta",
    "app": DBLROW_APP,
    "platform": PLATFORM,
    "timestamp": time.time(),
    "isElectron": False,
    "initialWindow": {"left": 0, "top": 0, "width": 900, "height": 700},
}

_METHOD_RE = re.compile(r"async (click\d+)\(\)\s*\{(.*?)\n    \}", re.S)
_STEP_RE = re.compile(r"await _step\('(\d+):(\w+) ([^']*)'.*?page\.(click\d+)\(\)\)")


def step_wdio_generate_doubleclick_row():
    print("\n[16] doubleClick on a native list row must not replay as a single click")
    request("DELETE", "/api/events")
    request("POST", "/api/events", DBLROW_SESSION_META)
    for ev in DBLROW_EVENTS:
        request("POST", "/api/events", ev)
    status, body = request("POST", "/api/generate", {
        "appName": DBLROW_APP,
        "platform": PLATFORM,
    }, timeout=30)
    check("POST /api/generate (doubleClick row) returns 200", status == 200, f"got {status}")
    if status != 200:
        check("(skipped doubleClick-row checks)", False, body.get("message", ""))
        return
    for f in body.get("files", []):
        fname, content = f.get("filename", ""), f.get("content", "")
        bodies = dict(_METHOD_RE.findall(content))
        click_fn = dbl_fn = None
        for _num, action, label, fn in _STEP_RE.findall(content):
            if action == "click" and "RowA" in label:
                click_fn = fn
            elif action == "doubleClick" and "RowB" in label:
                dbl_fn = fn
        if not click_fn or not dbl_fn:
            check(f"  {fname} emits both the click and the doubleClick step",
                  False, f"click={click_fn} doubleClick={dbl_fn}")
            continue
        norm_click = bodies.get(click_fn, "").replace("RowA", "ROW").strip()
        norm_dbl = bodies.get(dbl_fn, "").replace("RowB", "ROW").strip()
        check(
            f"  {fname} replays a doubleClick row differently from a click row",
            norm_click != norm_dbl,
            "the two generated calls are byte-identical — 'doubleClick' survives "
            "only in the log label, so a recorded double-click reaches the app as "
            "a single click. On a native list that selects the row instead of "
            "opening it, and every later step that depended on the navigation "
            "fails (7-Zip 컴퓨터→C:, reproduced identically twice on 2026-08-04)",
        )
        check(
            f"  {fname} embeds an osScopedInvoke.py that accepts a double-click flag",
            "--double" in content,
            "the helper has no way to express a double click — send_input_click() "
            "sends exactly one down/up pair and returns True, which also makes the "
            "InvokePattern fallback beneath it unreachable (osScopedInvoke.py "
            "invoke_item, 2026-07-24)",
        )


# Window-filling-container scenario (2026-08-04, 7-Zip regression run).
# stripWindowFillingContainers() correctly turns a click that resolved to a
# real container (Pane/Group/...) covering >=80% of the window into an
# explicit FAIL — no usable target, don't fabricate a coordinate click. But a
# click that resolved to controlType='Window' itself (the top-level window)
# is different: it's an activation/focus click, and replay already
# activates/normalizes the window on launch and on every window switch, so
# there is nothing left to replay. Turning THAT into a FAIL step meant an
# otherwise-perfect run could never report PASS (measured: 7-Zip's first
# captured click was exactly this — the window body before any menu
# interaction — and it alone kept the whole run at [FAIL]).
WINCLICK_APP = "MockWindowClick"
WINCLICK_EVENTS = [
    make_event("click", name="MyApp", automation_id="", class_name="",
               control_type="Window", window_title="MyApp",
               app_name=WINCLICK_APP, x=40, y=20, index=1),
    make_event("click", name="root", automation_id="", class_name="",
               control_type="Pane", window_title="MyApp",
               app_name=WINCLICK_APP, x=50, y=40, index=2),
    make_event("click", name="OK", automation_id="ok1", class_name="Button",
               control_type="Button", window_title="MyApp",
               app_name=WINCLICK_APP, x=60, y=60, index=3),
]
WINCLICK_EVENTS[0]["element"]["rect"] = [0, 0, 1000, 800]
WINCLICK_EVENTS[1]["element"]["rect"] = [0, 0, 1000, 800]
WINCLICK_EVENTS[2]["element"]["rect"] = [10, 10, 90, 40]
WINCLICK_SESSION_META = {
    "action": "session_meta",
    "app": WINCLICK_APP,
    "platform": PLATFORM,
    "timestamp": time.time(),
    "isElectron": False,
    "initialWindow": {"left": 0, "top": 0, "width": 1000, "height": 800},
}


def step_wdio_generate_window_click():
    print("\n[17] a click on the top-level window itself is dropped, not FAILed")
    request("DELETE", "/api/events")
    request("POST", "/api/events", WINCLICK_SESSION_META)
    for ev in WINCLICK_EVENTS:
        request("POST", "/api/events", ev)
    status, body = request("POST", "/api/generate", {
        "appName": WINCLICK_APP,
        "platform": PLATFORM,
    }, timeout=30)
    check("POST /api/generate (window click) returns 200", status == 200, f"got {status}")
    if status != 200:
        check("(skipped window-click checks)", False, body.get("message", ""))
        return
    for f in body.get("files", []):
        fname, content = f.get("filename", ""), f.get("content", "")
        fails = re.findall(r"_failures\.push\('(\d+):(\w+):no-selector'\)", content)
        check(
            f"  {fname} has exactly one no-selector FAIL (the Pane, not the Window)",
            len(fails) == 1,
            f"expected 1 (the real container), got {len(fails)}: {fails} — a click "
            "resolved to the top-level Window itself is an activation click "
            "replay already performs on launch/switch, so it must be dropped "
            "rather than turned into a FAIL step",
        )
        check(
            f"  {fname} still runs the real OK click after the dropped Window step",
            '"name":"OK"' in content or "'OK'" in content or "OK" in content,
            "the real click after the container clicks must still be generated",
        )


# Trigger-click-vanishes-behind-a-standalone-expandCollapse scenario
# (2026-07-29, HeidiSQL "더보기" SplitButton -> native popup menu ->
# "환경설정" MenuItem follow-up). mergeExpandCollapseClicks() runs first and
# correctly leaves a cross-window ExpandCollapsePattern MenuItem (one whose
# "item" candidate turned out to belong to yet ANOTHER window — a real
# dialog launch, not a dropdown selection) as its own standalone toggle.
# mergeCrossWindowTriggerClicks() then ran SECOND and, not knowing that
# event was already finalized, wrongly re-merged it as the "cross-window
# item" for the PLAIN click immediately preceding it — silently dropping
# that plain click (its data lands in crossWindowTrigger, which the
# expandCollapse renderer never reads), so the trigger that must physically
# open the popup before the item can be found never gets clicked at replay.
TRIGGER_EXPAND_APP = "MockTriggerExpand"
TRIGGER_EXPAND_EVENTS = [
    # generateWdio derives recordedRect from the FIRST event carrying integer
    # win*/Top/Width/Height (not session_meta, if any event has geometry —
    # see the 2026-07-21 launch-animation comment above recordedRect's
    # computation) — this trigger click MUST carry the real main-window
    # geometry, matching TRIGGER_EXPAND_SESSION_META.initialWindow, or the
    # popup event right after it gets picked as the "main window" instead
    # and every cross-window check below inverts.
    make_event("click", name="More", automation_id="btnMore", class_name="Button",
               control_type="Button", app_name=TRIGGER_EXPAND_APP, index=1,
               winLeft=100, winTop=100, winWidth=600, winHeight=400),
    make_event("click", name="", automation_id="473", class_name="",
               control_type="MenuItem", app_name=TRIGGER_EXPAND_APP,
               expand_collapse=True, index=2,
               winLeft=999, winTop=999, winWidth=50, winHeight=50),
    make_event("click", name="Log", automation_id="", class_name="",
               control_type="Button", app_name=TRIGGER_EXPAND_APP, index=3,
               winLeft=500, winTop=500, winWidth=200, winHeight=150),
]
# mergeExpandCollapseClicks's own sibling-vs-item guard (looksLikeSiblingNotItem)
# is what rejects pairing event 2 with event 3 as a dropdown item in the real
# HeidiSQL capture — the item ("로그 기록") sits well ABOVE the trigger
# ("더보기" popup entry) on screen (different top-level window entirely), so
# itemRect.top < triggerRect.bottom. rootHwndHex is deliberately left unset on
# both (would flip needsSessionSwitching() to session mode, which isn't what
# HeidiSQL's actual capture used here — this app must stay in simple mode to
# match reality).
TRIGGER_EXPAND_EVENTS[1]["element"]["rect"] = [3578, 737, 3805, 759]
TRIGGER_EXPAND_EVENTS[2]["element"]["rect"] = [2065, 124, 2151, 144]
TRIGGER_EXPAND_SESSION_META = {
    "action": "session_meta",
    "app": TRIGGER_EXPAND_APP,
    "platform": PLATFORM,
    "timestamp": time.time(),
    "isElectron": False,
    "initialWindow": {"left": 100, "top": 100, "width": 600, "height": 400},
}

# Nameless-item-as-itemName scenario (2026-07-29, HeidiSQL character-encoding
# ComboBox: its dropdown list items are owner-drawn and expose a numeric
# automationId — itself just the control's own hwnd, e.g. "1576746" — but NO
# Name at all). osExpandCollapse.py's item search is UIA_NameProperty-only,
# so falling back to automationId as itemName always failed with a
# misleading "item not found" instead of the honest "no name to search for".
NAMELESS_ITEM_APP = "MockNamelessItem"
NAMELESS_ITEM_EVENTS = [
    make_event("click", name="Combo2", automation_id="fakeTrigger",
               class_name="ComboBox", control_type="ComboBox",
               app_name=NAMELESS_ITEM_APP, expand_collapse=True, index=1),
    make_event("click", name="", automation_id="1576746", class_name="",
               control_type="ListItem", app_name=NAMELESS_ITEM_APP, index=2),
]
NAMELESS_ITEM_SESSION_META = {
    "action": "session_meta",
    "app": NAMELESS_ITEM_APP,
    "platform": PLATFORM,
    "timestamp": time.time(),
    "isElectron": False,
    "initialWindow": {"left": 100, "top": 100, "width": 600, "height": 400},
}

# Owner-drawn dropdown scenario (2026-07-31, HeidiSQL 네트워크 유형 combo =
# Win32 ComboBoxEx). Two stacked controls share one rect: the outer TComboBoxEx
# (UIA Pane, Name = the CURRENTLY SELECTED value, automationId = its own hwnd)
# and the inner ComboBox (Name and automationId both empty) which is the only
# one supporting ExpandCollapse. The open list publishes its 18 items, but every
# item Name is EMPTY, so an item can only be addressed by its position.
#
# Two failures this scenario pins down, both measured live:
#   - capture: the click lands below the combo's collapsed rect, so agent.py's
#     "point outside the adopted rect" guard used to discard the selector and
#     the event degraded into a click on the panel underneath.
#   - replay: a selector built from the outer control's Name
#     (`Pane[@ClassName="TComboBoxEx" and @Name="Microsoft SQL Server (TCP/IP)"]`)
#     can never match, because that Name only equals the value AFTER it has
#     been selected.
# agent.py now records comboItemIndex/comboItemCount and codegen must forward
# both to osExpandCollapse() so the helper expands and picks by position.
COMBO_INDEX_APP = "MockComboIndex"
COMBO_INDEX_EVENTS = [
    make_event("click", name="", automation_id="", class_name="ComboBox",
               control_type="ComboBox", app_name=COMBO_INDEX_APP,
               expand_collapse=True, index=1),
    # An ordinary click right after it. This must survive as its own step: the
    # combo event is already a complete action, so it must not be treated as a
    # bare trigger and paired with whatever the user did next.
    make_event("click", name="Save", automation_id="btnSave", class_name="Button",
               control_type="Button", app_name=COMBO_INDEX_APP, index=2),
]
COMBO_INDEX_EVENTS[0]["element"]["comboItemIndex"] = 4
COMBO_INDEX_EVENTS[0]["element"]["comboItemCount"] = 18
COMBO_INDEX_EVENTS[0]["element"]["comboItemName"] = ""
COMBO_INDEX_SESSION_META = {
    "action": "session_meta",
    "app": COMBO_INDEX_APP,
    "platform": PLATFORM,
    "timestamp": time.time(),
    "isElectron": False,
    "initialWindow": {"left": 100, "top": 100, "width": 600, "height": 400},
}

# hwnd-id trigger scenario (2026-07-29, HeidiSQL "더보기" SplitButton, 3차):
# the target/triggerTarget object builders for the COM helpers
# (osScopedInvoke/osExpandCollapse) read el.automationId directly — a
# SEPARATE code path from wdioSelectorById/ByClass, so isWindowHandleId's
# guard never covered it. A trigger whose automationId equals its own hwnd
# (reassigned every launch) got embedded verbatim AND, because an
# automationId was present, its Name (the one thing that WOULD still work)
# was also dropped by the state-dependent-name protection — "trigger not
# found" every time, confirmed live.
HWND_TRIGGER_APP = "MockHwndTrigger"
HWND_TRIGGER_EVENTS = [
    make_event("click", name="More", automation_id="9988776", class_name="SplitButton",
               control_type="SplitButton", app_name=HWND_TRIGGER_APP,
               winLeft=100, winTop=100, winWidth=600, winHeight=400, index=1),
    make_event("click", name="Prefs", automation_id="", class_name="",
               control_type="MenuItem", app_name=HWND_TRIGGER_APP,
               expand_collapse=True, index=2,
               winLeft=999, winTop=999, winWidth=50, winHeight=50),
]
HWND_TRIGGER_EVENTS[0]["element"]["hwnd"] = 9988776  # equals its own automationId -> unstable
HWND_TRIGGER_SESSION_META = {
    "action": "session_meta",
    "app": HWND_TRIGGER_APP,
    "platform": PLATFORM,
    "timestamp": time.time(),
    "isElectron": False,
    "initialWindow": {"left": 100, "top": 100, "width": 600, "height": 400},
}

# Volatile popup-MenuItem-id scenario (2026-08-04, HeidiSQL "더 보기" overflow
# menu, follow-up to HWND_TRIGGER_APP above). The item's AutomationId here is
# a per-session control-creation counter (numeric, hwnd=0) — same shape as
# the HWND_TRIGGER item's hwnd-id disease, but the item ALSO has no Name
# (icon-only overflow menu item), which is what makes this scenario
# necessary: it proves the id gets rejected WITHOUT starving
# mergeCrossWindowTriggerClicks() of the "something is here, pair it with
# its trigger" signal that a real (if untrustworthy) id/name still provides.
# A first attempt at this fix (2026-08-04) cleared the id at CAPTURE time in
# agent.py, which made the item look like it had nothing at all — the merge
# never fired, the trigger click vanished, and the item was left standalone
# with a totally empty selector. Rejecting it here (selector-build time)
# instead keeps the merge intact.
VOLATILE_MENUITEM_APP = "MockVolatileMenuItem"
VOLATILE_MENUITEM_EVENTS = [
    make_event("click", name="More", automation_id="", class_name="SplitButton",
               control_type="SplitButton", app_name=VOLATILE_MENUITEM_APP,
               winLeft=100, winTop=100, winWidth=600, winHeight=400, index=1),
    make_event("click", name="", automation_id="477", class_name="",
               control_type="MenuItem", app_name=VOLATILE_MENUITEM_APP,
               expand_collapse=True, index=2,
               winLeft=999, winTop=999, winWidth=50, winHeight=50),
]
VOLATILE_MENUITEM_EVENTS[1]["element"]["hwnd"] = 0
VOLATILE_MENUITEM_SESSION_META = {
    "action": "session_meta",
    "app": VOLATILE_MENUITEM_APP,
    "platform": PLATFORM,
    "timestamp": time.time(),
    "isElectron": False,
    "initialWindow": {"left": 100, "top": 100, "width": 600, "height": 400},
}


def step_wdio_generate_volatile_menuitem_id():
    print("\n[16] a volatile popup-MenuItem id is rejected without dropping the trigger pairing (HeidiSQL 더보기, 2026-08-04)")
    request("DELETE", "/api/events")
    request("POST", "/api/events", VOLATILE_MENUITEM_SESSION_META)
    for ev in VOLATILE_MENUITEM_EVENTS:
        request("POST", "/api/events", ev)
    status, body = request("POST", "/api/generate", {
        "appName": VOLATILE_MENUITEM_APP,
        "platform": PLATFORM,
    }, timeout=30)
    check("POST /api/generate (volatile menuitem) returns 200", status == 200, f"got {status}")
    if status != 200:
        check("(skipped volatile-menuitem checks)", False, body.get("message", ""))
        return
    for f in body.get("files", []):
        fname, content = f.get("filename", ""), f.get("content", "")
        check(
            f"  {fname} never embeds the item's volatile counter id as its automationId",
            '"automationId":"477"' not in content,
            "a popup MenuItem's numeric AutomationId with hwnd=0 is a "
            "per-session control-creation counter, not a stable id — "
            "measured 2026-08-04: the same menu opened 3 times in one "
            "recording session yielded 474, 475, 477 for the SAME item",
        )
        check(
            f"  {fname} still pairs the item with its opening trigger",
            '"name":"More"' in content,
            "the trigger click ('더 보기'/More) must survive in the "
            "generated triggerTarget — a first attempt at rejecting the "
            "volatile id cleared it at capture time instead of selector-"
            "build time, which starved mergeCrossWindowTriggerClicks() of "
            "the signal it needs to pair trigger+item, and the trigger "
            "click silently vanished from the generated test entirely",
        )


# Post-navigation windowTitle on a position-resolved menu item (2026-08-05,
# FileZilla 파일(F) -> 사이트 관리자(S)...). agent.py resolves a menu pick into
# ONE event carrying menuItemIndex/menuItemCount, and codegen's
# COMBO_OPEN_ACTIONS block drops the preceding trigger click as redundant.
# But the item click's own windowTitle is captured by the worker thread AFTER
# the item has already opened its dialog, so the hit-test reports the NEW
# window's title ("사이트 관리자") even though the 파일 menu itself lives in the
# main "FileZilla" window — the same post-navigation race dedupeDoubleClicks
# already guards against by trusting only the earliest constituent click.
#
# Dropping the trigger threw away the only event that still had the correct
# title, so the surviving merged event became filtered[0] and its corrupted
# title flowed straight into launchFrag/_mainTitleFrag for the entire script.
# Measured live: the generated test called
# launchApp(..., "사이트 관리자", ...) and waited 8 polls for a window that
# cannot exist yet ("[launch] window not detected within timeout"), then
# _hwndCache["사이트 관리자"] stayed empty and every later step died with
# "no window hwnd" / "window not found — failing fast". The app never even
# came up as far as the test was concerned.
POSTNAV_TITLE_APP = "MockPostNavTitle"
POSTNAV_TITLE_EXE = "C:\\Program Files\\FileZilla FTP Client\\filezilla.exe"
POSTNAV_TITLE_EVENTS = [
    # The trigger: 파일(F) menu bar item, correctly attributed to the main window.
    make_event("click", name="File", automation_id="", class_name="",
               control_type="MenuItem", window_title="MainWin",
               app_name=POSTNAV_TITLE_APP, expand_collapse=True, index=1,
               winLeft=100, winTop=100, winWidth=600, winHeight=400),
    # The item pick: same rect (menu bar item), already resolved to
    # "expand this menu, take item #0 of 8" — but its windowTitle was
    # hit-tested after the dialog it opens had already appeared.
    make_event("click", name="File", automation_id="", class_name="",
               control_type="MenuItem", window_title="SiteManagerDlg",
               app_name=POSTNAV_TITLE_APP, expand_collapse=True, index=2,
               winLeft=100, winTop=100, winWidth=600, winHeight=400),
]
for _ev in POSTNAV_TITLE_EVENTS:
    _ev["element"]["rect"] = [572, 100, 637, 124]
POSTNAV_TITLE_EVENTS[1]["element"]["menuItemIndex"] = 0
POSTNAV_TITLE_EVENTS[1]["element"]["menuItemCount"] = 8
POSTNAV_TITLE_EVENTS[1]["element"]["menuItemName"] = "Site Manager\tCtrl+S"
POSTNAV_TITLE_SESSION_META = {
    "action": "session_meta",
    "app": POSTNAV_TITLE_APP,
    "platform": PLATFORM,
    "timestamp": time.time(),
    "isElectron": False,
    "initialWindow": {"left": 100, "top": 100, "width": 600, "height": 400},
}


# Reused-DropDown-in-the-same-window scenario (2026-07-29, HeidiSQL "새 세션"
# dialog: the network-type combo and the encoding combo sit one above the
# other, both exposing a dropdown arrow with automationId="DropDown" — WAD's
# 'accessibility id' lookup (the plain click path) has no way to tell them
# apart and always resolves to whichever one FindFirst happens to return
# first). Neither click here is cross-window relative to the main window
# (both open/close in place), so this exercises the isComboDropDownArrow
# same-window branch specifically, not the cross-window trigger merge path.
DUP_DROPDOWN_APP = "MockDupDropdown"
DUP_DROPDOWN_EVENTS = [
    make_event("click", name="닫기", automation_id="DropDown", class_name="",
               control_type="Button", app_name=DUP_DROPDOWN_APP, index=1,
               relX=663, relY=84),
    make_event("click", name="열기", automation_id="DropDown", class_name="",
               control_type="Button", app_name=DUP_DROPDOWN_APP, index=2,
               relX=663, relY=112),
]
DUP_DROPDOWN_SESSION_META = {
    "action": "session_meta",
    "app": DUP_DROPDOWN_APP,
    "platform": PLATFORM,
    "timestamp": time.time(),
    "isElectron": False,
    "initialWindow": {"left": 100, "top": 100, "width": 600, "height": 400},
}

# TComboBoxEx re-click scenario (2026-07-31, HeidiSQL 네트워크 유형 콤보):
# a click that lands on the combo's own body (not the arrow, not an open
# list item) hit-tests to the TComboBoxEx Pane itself, whose Name is
# whatever value is CURRENTLY selected and whose automationId is its own
# window handle (unstable). A selector built from that Name can only ever
# match after the value has already been chosen — never at replay start.
# Real failure reproduced live: 'click-not-found://Pane[@ClassName=
# "TComboBoxEx" and @Name="MariaDB or MySQL (SSH tunnel)"]'.
COMBOBOXEX_RECLICK_APP = "MockComboBoxExReclick"
COMBOBOXEX_RECLICK_EVENTS = [
    make_event("click", name="MariaDB or MySQL (SSH tunnel)", automation_id="",
               class_name="TComboBoxEx", control_type="Pane",
               app_name=COMBOBOXEX_RECLICK_APP, index=1, relX=663, relY=84),
]
COMBOBOXEX_RECLICK_EVENTS[0]["element"]["hwnd"] = 2690052  # equals nothing here,
# but mirrors the live capture where automationId WOULD equal the element's
# own hwnd if one were assigned — isWindowHandleId only fires when
# automationId is set, so this scenario leaves automationId empty (the more
# common capture shape) and relies on forceDropName to prove Name is dropped
# regardless of automationId state.
COMBOBOXEX_RECLICK_SESSION_META = {
    "action": "session_meta",
    "app": COMBOBOXEX_RECLICK_APP,
    "platform": PLATFORM,
    "timestamp": time.time(),
    "isElectron": False,
    "initialWindow": {"left": 100, "top": 100, "width": 600, "height": 400},
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

# Simple-mode cross-window click carrying a rootHwndHex from agent.py's PID/
# install-dir self-heal (2026-07-21, real 7-Zip Benchmark repro: clicking
# "Cancel" in the Benchmark dialog). needsSessionSwitching() correctly stays
# in SIMPLE mode here (only ONE distinct rootHwndHex value appears across the
# whole recording, so roots.size===1 -> False) — but simple mode has no
# _switchWindow()/getWindowSession() at all, only a single _appSid session
# scoped to the ORIGINAL main window. The cross-window click branch's guard
# (added 2026-07-21 to fix a SESSION-mode title-collision regression) used to
# exclude ANY event carrying rootHwndHex unconditionally, leaving this event
# with no valid replay path — it fell through to the plain click branch,
# which can only ever search the main window's own session, producing
# `click-not-found` for a button that lives in a different top-level window.
SIMPLE_ROOTHWND_APP = "MockSimpleRootHwnd"
SIMPLE_ROOTHWND_EVENTS = [
    make_event("click", name="", automation_id="", class_name="",
               window_title="Main", app_name=SIMPLE_ROOTHWND_APP, x=100, y=100, index=1,
               winLeft=0, winTop=0, winWidth=800, winHeight=600),
    # Benchmark-dialog Cancel button — different rect than the main window,
    # and tagged with a rootHwndHex agent.py's self-heal resolved (the ONLY
    # rootHwndHex anywhere in this recording, so session mode never triggers).
    make_event("click", name="취소", automation_id="2", class_name="Button",
               window_title="Benchmark", app_name=SIMPLE_ROOTHWND_APP, x=400, y=400, index=2,
               winLeft=300, winTop=300, winWidth=400, winHeight=300,
               rootHwndHex="137900"),
]

# dialogRects corruption via same-titled trigger+popup merge (2026-07-21,
# real 7-Zip GUI repro): clicking "추가" (Add) in the MAIN window pops open a
# small confirmation dialog with an "확인" (OK) button — and that small
# dialog happens to carry the exact same literal title text as the main
# window itself ("7-Zip"), a title collision. mergeCrossWindowTriggerClicks
# merges the trigger ("추가", non-cross-window, main window rect) with the
# item ("확인", cross-window, small-dialog rect) into ONE event, which
# consumes the trigger and keeps only the small dialog's own geometry. The
# later dialogRects scan (keyed purely by window TITLE) walks the merged
# `filtered` list looking for the FIRST rect seen under title "7-Zip" — with
# the trigger's entry gone, it picks up the small dialog's rect instead of
# the real main window's, and _ensureDialog() then shrinks the ACTUAL main
# window down to that tiny size on the next same-titled segment boundary,
# freezing the whole replay.
TITLE_COLLISION_DIALOGRECT_APP = "MockTitleCollisionDialogRect"
TITLE_COLLISION_DIALOGRECT_EVENTS = [
    make_event("click", name="추가", automation_id="", class_name="Button",
               window_title="7-Zip", app_name=TITLE_COLLISION_DIALOGRECT_APP, x=100, y=100, index=1,
               winLeft=2370, winTop=-415, winWidth=1152, winHeight=592),
    # Small "OK" dialog — SAME literal title as the main window, but a much
    # smaller rect and its own tracked rootHwndHex (a genuinely different,
    # watcher-tracked top-level window).
    make_event("click", name="확인", automation_id="", class_name="",
               window_title="7-Zip", app_name=TITLE_COLLISION_DIALOGRECT_APP, x=120, y=120, index=2,
               winLeft=2765, winTop=-214, winWidth=235, winHeight=163,
               rootHwndHex="AAAA", newWindowSegment=True),
    # Unrelated later dialog with its OWN distinct rootHwndHex, purely so
    # this recording has >=2 distinct rootHwndHex values and needsSessionSwitching()
    # actually enters session mode (dialogRects/_ensureDialog is session-mode-only).
    make_event("click", name="", automation_id="", class_name="",
               window_title="C:\\PerfLogs\\", app_name=TITLE_COLLISION_DIALOGRECT_APP, x=200, y=200, index=3,
               winLeft=2370, winTop=-415, winWidth=1152, winHeight=592,
               rootHwndHex="BBBB", newWindowSegment=True),
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
    # 2026-07-27: pre-existing copies of the 9 embedded-helper scripts sitting
    # directly in the app's own output folder (left by a generate from before
    # saveFiles() started routing them to _debug-helpers/) must also be
    # auto-removed on the next generate — plant one and check it's gone.
    with open(os.path.join(out_dir, "osWindowRect.ps1"), "w", encoding="utf-8") as fh:
        fh.write("# dummy pre-2026-07-27 helper copy planted by mock_events.py\n")

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
    debug_dir = os.path.join(repo_root, "generated-wdio", "_debug-helpers", APP_NAME)
    check(
        "app's own output folder has no os*.ps1/os*.py helper copies",
        not any(os.path.exists(os.path.join(out_dir, n)) for n in DEBUG_HELPER_NAMES),
        f"checked {out_dir} for {DEBUG_HELPER_NAMES}",
    )
    check(
        "the 9 helper scripts were written to generated-wdio/_debug-helpers/ instead",
        all(os.path.exists(os.path.join(debug_dir, n)) for n in DEBUG_HELPER_NAMES),
        f"checked {debug_dir} for {DEBUG_HELPER_NAMES}",
    )
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
        check_helpers_defined(fname, content)
        check(
            f"  {fname} is portable — no sibling os*.ps1/os*.py file dependency",
            "join(__dirname, 'os" not in content,
            "generated file must resolve its os*.ps1/os*.py helpers through "
            "_helperFile(name), which embeds the script text and writes it to "
            "a temp dir at runtime — a join(__dirname, 'osX...') reference "
            "means copying just this .js elsewhere breaks it (2026-07-27: "
            "reported when moving FileZillaTestByClass.js out of its folder)",
        )
        check(
            f"  {fname} defines _helperFile() and embeds all 9 helper scripts",
            "function _helperFile(name)" in content
            and all(
                f"'{n}':" in content
                for n in (
                    "osWindowRect.ps1", "osMoveWindow.ps1", "osType.ps1",
                    "osEscape.ps1", "osActivate.ps1", "osDismissPopup.ps1",
                    "osScroll.py", "osExpandCollapse.py", "osScopedInvoke.py",
                )
            ),
            "missing _helperFile() or one of the 9 embedded helper-script entries",
        )
        # 2026-07-27: saveFiles() has prepended a UTF-8 BOM to .ps1 files since
        # 2026-07-08 because `powershell -File` reads a BOM-less file as the
        # system ANSI codepage (CP949), mangling osDismissPopup.ps1's Korean
        # button names and killing the PS parser outright. The new temp-
        # extraction path (_helperFile) initially wrote plain utf8 and silently
        # broke the whole popup Fail-and-Recover mechanism — reproduced against
        # a real temp copy before fixing. Guard the extraction path too.
        check(
            f"  {fname} writes extracted .ps1 helpers with a UTF-8 BOM",
            "\\ufeff" in content and ".endsWith('.ps1')" in content,
            "_helperFile() must prepend a BOM for .ps1 files — without it "
            "powershell reads them as CP949 and osDismissPopup.ps1's Korean "
            "button names ('취소'/'아니요'/'닫기') become mojibake that fails "
            "to parse, disabling popup recovery with only a warning in the log",
        )
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


def step_wdio_generate_simple_roothwnd():
    print("\n[8e] Simple-mode cross-window click with a rootHwndHex must "
          "still use osScopedInvoke, not the main-session-only plain click "
          "(2026-07-21, real 7-Zip Benchmark 'Cancel' repro)")
    request("DELETE", "/api/events")
    for ev in SIMPLE_ROOTHWND_EVENTS:
        request("POST", "/api/events", ev)
    status, body = request("POST", "/api/generate", {
        "appName": SIMPLE_ROOTHWND_APP,
        "platform": PLATFORM,
    }, timeout=30)
    check("POST /api/generate (simple-roothwnd) returns 200", status == 200, f"got {status}")
    if status != 200:
        check("(skipped simple-roothwnd checks)", False, body.get("message", ""))
        return
    files = body.get("files", [])
    for f in files:
        content = f.get("content", "")
        check(
            f"  {f.get('filename')} replays the Benchmark-dialog Cancel via "
            "osScopedInvoke, not a main-session-scoped click",
            'osScopedInvoke(_appHwnd, {"automationId":"2"' in content
            and 'Name="취소"' not in content,
            "expected the cross-window click branch to handle this (rect "
            "differs from the main window, and this recording never enters "
            "session mode since only one rootHwndHex value appears at all) "
            "— if it instead falls through to the plain click branch, it can "
            "only ever search _appSid (scoped to the ORIGINAL main window) "
            "and can never find a button living in a different top-level "
            "window, producing click-not-found",
        )


def step_wdio_generate_title_collision_dialogrect():
    print("\n[8f] dialogRects must keep the MAIN window's own rect for its "
          "title, not a same-titled popup's rect swallowed by trigger-merge "
          "(2026-07-21, real 7-Zip 'Add' -> 'OK' dialog repro)")
    request("DELETE", "/api/events")
    for ev in TITLE_COLLISION_DIALOGRECT_EVENTS:
        request("POST", "/api/events", ev)
    status, body = request("POST", "/api/generate", {
        "appName": TITLE_COLLISION_DIALOGRECT_APP,
        "platform": PLATFORM,
    }, timeout=30)
    check("POST /api/generate (title-collision-dialogrect) returns 200", status == 200, f"got {status}")
    if status != 200:
        check("(skipped title-collision-dialogrect checks)", False, body.get("message", ""))
        return
    files = body.get("files", [])
    for f in files:
        content = f.get("content", "")
        check(
            f"  {f.get('filename')} keeps the main window's own (large) rect "
            "for the '7-Zip' title in _dialogRects",
            '"7-Zip":{"left":2370,"top":-415,"width":1152,"height":592}' in content.replace(" ", ""),
            "expected _dialogRects['7-Zip'] to be the MAIN window's recorded "
            "geometry (2370,-415,1152,592) — if the small same-titled 'OK' "
            "dialog's rect (2765,-214,235,163) shows up instead, replay will "
            "shrink the REAL main window down to that tiny size the next "
            "time this title's segment boundary is hit, freezing everything "
            "after it",
        )
        check(
            f"  {f.get('filename')} does not let the small dialog's rect leak "
            "into _dialogRects['7-Zip']",
            '"7-Zip":{"left":2765,"top":-214,"width":235,"height":163}' not in content.replace(" ", ""),
            "the small 'OK' dialog's own geometry must not be stored under "
            "the main window's title key",
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
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    session_out_dir = os.path.join(repo_root, "generated-wdio", SESSION_APP)
    session_debug_dir = os.path.join(repo_root, "generated-wdio", "_debug-helpers", SESSION_APP)
    check(
        "app's own output folder has no os*.ps1/os*.py helper copies (session)",
        not any(os.path.exists(os.path.join(session_out_dir, n)) for n in DEBUG_HELPER_NAMES),
        f"checked {session_out_dir} for {DEBUG_HELPER_NAMES}",
    )
    check(
        "the 9 helper scripts were written to generated-wdio/_debug-helpers/ instead (session)",
        all(os.path.exists(os.path.join(session_debug_dir, n)) for n in DEBUG_HELPER_NAMES),
        f"checked {session_debug_dir} for {DEBUG_HELPER_NAMES}",
    )
    for f in files:
        fname = f.get("filename", "")
        content = f.get("content", "")
        check_helpers_defined(fname, content)
        check(
            f"  {fname} is portable — no sibling os*.ps1/os*.py file dependency",
            "join(__dirname, 'os" not in content,
            "generated file must resolve its os*.ps1/os*.py helpers through "
            "_helperFile(name) instead of a sibling-file join(__dirname, ...) "
            "reference (2026-07-27)",
        )
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
            "function osExpandCollapse(hwnd, target, itemName, itemIndex, itemCount)" in content,
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
            'osExpandCollapse(_appHwnd, {"automationId":"5999","className":"ComboBox","name":"Combo"}, "Red", null, null)' in content,
            "expected the 3 consecutive re-clicks of the same ComboBox trigger "
            "to collapse into ONE osExpandCollapse call whose itemName is the "
            "real item ('Red') that came after them — real FileZilla capture "
            "had 3 physical clicks on '배경색(B):' before it opened, and the "
            "old merge logic paired click #1 with click #2 (also just the "
            "trigger) instead of skipping ahead to the real item",
        )
        check(
            f"  {fname} never merges the trigger with itself (self-referencing itemName)",
            'osExpandCollapse(_appHwnd, {"automationId":"5999","className":"ComboBox","name":"Combo"}, "Combo", null, null)' not in content,
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
            'osExpandCollapse(_appHwnd, {"automationId":"","className":"MenuItem","name":"File"}, "Open", null, null)' in content,
            "the fix must not disturb the existing non-redundant merge path",
        )


def step_wdio_generate_postnav_title_keeps_trigger_window():
    print("\n[9d] A dropped trigger must not take the main window's title with it "
          "(2026-08-05 FileZilla 파일 -> 사이트 관리자 launch timeout)")
    request("DELETE", "/api/events")
    request("POST", "/api/events", POSTNAV_TITLE_SESSION_META)
    for ev in POSTNAV_TITLE_EVENTS:
        request("POST", "/api/events", ev)

    status, body = request("POST", "/api/generate", {
        "appName": POSTNAV_TITLE_APP,
        "exePath": POSTNAV_TITLE_EXE,
        "platform": PLATFORM,
    }, timeout=30)
    check("POST /api/generate (postnav-title) returns 200", status == 200, f"got {status}")
    if status != 200:
        check("(skipped postnav-title checks)", False, body.get("message", ""))
        return
    for f in body.get("files", []):
        fname, content = f.get("filename", ""), f.get("content", "")
        check(
            f"  {fname} launches/tracks the TRIGGER's window, not the dialog the item opened",
            '_mainTitleFrag = "MainWin"' in content,
            "the merged menu-pick event inherits the trigger's window because "
            "that is where the menu bar actually lives — its own windowTitle "
            "was hit-tested post-navigation and names the dialog the pick "
            "opens, which does not exist yet at launch time",
        )
        check(
            f"  {fname} never waits for the item's own dialog as the app's main window",
            '_mainTitleFrag = "SiteManagerDlg"' not in content
            and 'launchApp("C:\\\\Program Files\\\\FileZilla FTP Client\\\\filezilla.exe", [], "SiteManagerDlg"' not in content,
            "this is the exact 2026-08-05 failure: launchApp() polled 8 times "
            "for a window that only appears AFTER step 1 runs, gave up with "
            "'window not detected within timeout', and left _hwndCache empty "
            "so every later step failed with 'no window hwnd'",
        )
        check(
            f"  {fname} still collapses the trigger into a single position-resolved menu step",
            content.count("_step('") == 1 and "osExpandCollapse(" in content,
            "the trigger click must stay merged away (it is redundant — "
            "osExpandCollapse expands the menu itself); only its windowTitle "
            "is salvaged, not the step",
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
        # 2026-08-04: a CheckBox click now routes through osScopedInvoke's
        # verified_toggle_click (checkbox value-verification gap fix) instead
        # of a bare '~1049' accessibility-id click() — the numeric id itself
        # must still survive (not be rejected as a volatile slot index), just
        # embedded in the JSON selector this call carries instead of a
        # standalone '~id' string.
        check(
            f"  {fname} trusts a numeric AutomationId on a Button/CheckBox",
            '"automationId":"1049"' in content and 'osScopedInvoke(' in content,
            "stable Win32 resource ID (1049) was rejected as if it were a "
            "ListView slot index — breaks AutomationId-based XPath on "
            "native dialogs (PuTTY 2026-07-13)",
        )
        check(
            f"  {fname} routes the CheckBox click through verified_toggle_click (value-verification gap fix)",
            'osScopedInvoke(_appHwnd, {"automationId":"1049","className":"Button",'
            '"name":"System menu appears on ALT-Space"}, null, null, null, '
            '"Native Dialog", false, true);' in content,
            "a plain WAD element/click() reports success whenever the click "
            "itself doesn't error, without checking whether the checkbox's "
            "ToggleState actually changed — the same false-PASS risk measured "
            "on TeamViewer's WebView2 toggles (2026-07-31) is structurally "
            "present on every native CheckBox too (2026-08-04)",
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
        # 2026-08-04: this id (1049) is a CheckBox, which no longer emits a
        # bare '~id' selector at all (see the verified_toggle_click checks
        # above) — the still-relevant regression to guard is that the reused-id
        # AND-condition machinery (Host:/Port: 5999 above) doesn't ALSO fire on
        # this unrelated, non-reused id and mangle its selector.
        check(
            f"  {fname} does not AND a Name onto the NON-reused id 1049 (regression)",
            '"automationId":"1049","className":"Button","name":"System menu appears on ALT-Space"' in content,
            "the reuse-detection must not over-trigger on a numeric id that "
            "only appears once — that would needlessly complicate a selector "
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
            'osExpandCollapse(_appHwnd, {"automationId":"","className":"TreeItem","name":"Window"}, null, null, null)' in content,
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
            '{"automationId":"DropDown","className":"","name":""}, null, null, "Native Dialog");' in content,
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
            '{"automationId":"DropDown","className":"","name":""}, null, null, "Native Dialog");' in content
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


def step_wdio_generate_vcl_hwnd_id():
    print("\n[11] Delphi/VCL hwnd-as-AutomationId rejection (HeidiSQL follow-up)")
    request("DELETE", "/api/events")
    request("POST", "/api/events", VCL_SESSION_META)
    for ev in VCL_EVENTS:
        request("POST", "/api/events", ev)

    status, body = request("POST", "/api/generate", {
        "appName": VCL_APP,
        "platform": PLATFORM,
    }, timeout=30)
    check("POST /api/generate (VCL hwnd-id) returns 200", status == 200, f"got {status}")
    if status != 200:
        check("(skipped VCL hwnd-id checks)", False, body.get("message", ""))
        return
    files = body.get("files", [])
    for f in files:
        fname = f.get("filename", "")
        content = f.get("content", "")
        check(
            f"  {fname} never emits the control's own hwnd as an accessibility-id selector",
            "'~1051972'" not in content,
            "automationId 1051972 equals the control's own NativeWindowHandle "
            "(HeidiSQL TVirtualStringTree) — HWNDs are reassigned every "
            "launch, so this selector can never match at replay time "
            "(2026-07-29)",
        )
        check(
            f"  {fname} falls back to the stable ClassName for the hwnd-id control",
            'ClassName="TVirtualStringTree"' in content,
            "with the unstable hwnd-id rejected, the selector chain must "
            "still fall through to className (no name/anchor captured here)",
        )
        # ByClass prioritizes a ClassName+Name combo over automationId when
        # both are present (independent, pre-existing behavior) — this
        # regression guard only makes sense for ById, which tries
        # automationId first. Mirrors the equivalent NATIVE_APP check above.
        if "ById" in fname:
            check(
                f"  {fname} still trusts a real Win32 resource id whose value differs from its own hwnd (regression)",
                "'~1049'" in content,
                "the guard must be narrow — a stable numeric AutomationId that "
                "does NOT equal its own hwnd (PuTTY/7-Zip/FileZilla pattern) "
                "must keep resolving to the bare '~id' form",
            )


def step_wdio_generate_trigger_expand_merge_order():
    print("\n[12] Trigger + cross-window expandCollapse item merge into one osScopedInvoke call (HeidiSQL 더보기 follow-up, 2차)")
    request("DELETE", "/api/events")
    request("POST", "/api/events", TRIGGER_EXPAND_SESSION_META)
    for ev in TRIGGER_EXPAND_EVENTS:
        request("POST", "/api/events", ev)

    status, body = request("POST", "/api/generate", {
        "appName": TRIGGER_EXPAND_APP,
        "platform": PLATFORM,
    }, timeout=30)
    check("POST /api/generate (trigger-expand order) returns 200", status == 200, f"got {status}")
    if status != 200:
        check("(skipped trigger-expand checks)", False, body.get("message", ""))
        return
    files = body.get("files", [])
    for f in files:
        fname = f.get("filename", "")
        content = f.get("content", "")
        # 2026-07-29 (2차): the first fix (keep the trigger as its own
        # standalone step) was incomplete — the trigger and the item live in
        # ACTUALLY DIFFERENT windows (a popup the trigger itself opens), so
        # they must run in ONE process via osScopedInvoke(item, trigger),
        # exactly like PuTTY's DropDown->ComboLBox pattern. Splitting them
        # into two separate steps/processes means the second process's
        # "new top-level window" baseline is captured AFTER the first
        # process already opened the popup — so it's never seen as "new" and
        # the item search fails every time (measured live against HeidiSQL).
        check(
            f"  {fname} merges the trigger and the cross-window expandCollapse item into one osScopedInvoke call",
            # automationId "" not "473" (2026-08-04): a popup MenuItem's numeric
            # id with hwnd=0 is a volatile per-session counter (isVolatileMenuItemId)
            # — rejected here same as the item's own Name (empty, icon-only item).
            # The merge itself (trigger+item, one osScopedInvoke call) is the
            # thing this check actually guards; see MockVolatileMenuItem for the
            # id-rejection assertion in isolation.
            'osScopedInvoke(_appHwnd, {"automationId":"","className":"","name":""}, {"automationId":"btnMore","className":"Button","name":""}, null, null, "Calculator");' in content,
            "trigger (More) and item (the cross-window standalone toggle) "
            "must run in the SAME process so the popup the trigger opens is "
            "visible to the item search's new-window baseline",
        )
        check(
            f"  {fname} never renders the merged item as a standalone osExpandCollapse call",
            'osExpandCollapse(_appHwnd, {"automationId":"473"' not in content,
            "the expandCollapse render path doesn't read crossWindowTrigger — "
            "taking it here instead of osScopedInvoke would silently drop "
            "the trigger click again (the original 2026-07-29 bug)",
        )
        check(
            f"  {fname} still replays the later, genuinely separate cross-window click on its own",
            'osScopedInvoke(_appHwnd, {"automationId":"","className":"","name":"Log"}, null, null, null, "Calculator");' in content,
            "the fix must not disturb a real, unrelated cross-window click "
            "that just happens to follow a merged trigger+item pair",
        )


def step_wdio_generate_nameless_item_no_fake_itemname():
    print("\n[13] Nameless dropdown item never becomes a fake itemName (HeidiSQL encoding combo follow-up)")
    request("DELETE", "/api/events")
    request("POST", "/api/events", NAMELESS_ITEM_SESSION_META)
    for ev in NAMELESS_ITEM_EVENTS:
        request("POST", "/api/events", ev)

    status, body = request("POST", "/api/generate", {
        "appName": NAMELESS_ITEM_APP,
        "platform": PLATFORM,
    }, timeout=30)
    check("POST /api/generate (nameless item) returns 200", status == 200, f"got {status}")
    if status != 200:
        check("(skipped nameless-item checks)", False, body.get("message", ""))
        return
    files = body.get("files", [])
    for f in files:
        fname = f.get("filename", "")
        content = f.get("content", "")
        check(
            f"  {fname} never searches for a raw automationId as if it were a Name",
            '"1576746")' not in content and "'1576746')" not in content,
            "osExpandCollapse.py's item search is UIA_NameProperty-only — "
            "passing the item's numeric automationId as itemName can never "
            "match anything and produces a misleading 'item not found' "
            "instead of an honest failure (HeidiSQL encoding ComboBox, "
            "2026-07-29)",
        )
        check(
            f"  {fname} keeps the trigger as a standalone toggle instead of a bogus merge",
            'osExpandCollapse(_appHwnd, {"automationId":"fakeTrigger","className":"ComboBox","name":"Combo2"}, null, null, null)' in content,
            "with no real item name to merge, the trigger must fall back to "
            "the plain expand/collapse toggle it always had as a valid "
            "standalone behavior",
        )
        check(
            f"  {fname} lets the nameless item fall through to the dedicated ListItem COM route",
            'osScopedInvoke(_appHwnd, {"automationId":"1576746","className":"","name":""})' in content,
            "no longer consumed by the (rejected) expandCollapse merge, this "
            "ListItem now reaches the existing 2026-07-15 direct-Invoke route "
            "(WAD's element/click is unreliable on native list rows) — a "
            "real attempt using the only identifying data available "
            "(automationId, via COM property search, NOT the Name-only "
            "search that made the old itemName fallback pointless) instead "
            "of either a bogus text search or giving up outright",
        )


def step_wdio_generate_owner_drawn_dropdown_by_index():
    print("\n[16] Owner-drawn dropdown item selected by position (HeidiSQL ComboBoxEx)")
    request("DELETE", "/api/events")
    request("POST", "/api/events", COMBO_INDEX_SESSION_META)
    for ev in COMBO_INDEX_EVENTS:
        request("POST", "/api/events", ev)

    status, body = request("POST", "/api/generate", {
        "appName": COMBO_INDEX_APP,
        "platform": PLATFORM,
    }, timeout=30)
    check("POST /api/generate (combo index) returns 200", status == 200, f"got {status}")
    if status != 200:
        check("(skipped combo-index checks)", False, body.get("message", ""))
        return
    for f in body.get("files", []):
        fname = f.get("filename", "")
        content = f.get("content", "")
        check(
            f"  {fname} forwards the recorded list position to osExpandCollapse()",
            ", 4, 18)" in content and "osExpandCollapse(" in content,
            "an owner-drawn dropdown exposes no item Names at all, so the only "
            "way to pick a value is the item's position in the open list — "
            "codegen must pass comboItemIndex/comboItemCount through or the "
            "step silently degrades to 'just open the dropdown' (HeidiSQL "
            "network-type combo, 2026-07-31)",
        )
        check(
            f"  {fname} never builds a selector from the combo's state-dependent Name",
            "TComboBoxEx" not in content,
            "the outer ComboBoxEx wrapper's Name IS the currently selected "
            "value, so a selector using it can only match AFTER the value has "
            "been chosen — the exact chicken-and-egg failure measured on "
            "replay ('click-not-found://Pane[@ClassName=\"TComboBoxEx\" and "
            "@Name=\"Microsoft SQL Server (TCP/IP)\"]')",
        )
        check(
            f"  {fname} does not swallow the following click as this dropdown's item",
            # ById resolves it as '~btnSave', ByClass as a ClassName+Name XPath —
            # assert on the step label, which both variants share.
            "2:click Save" in content,
            "the event already encodes a complete 'expand then pick #N' "
            "action; letting mergeExpandCollapseClicks pair it with the next "
            "click would consume an unrelated user action ('Save') as if it "
            "were this dropdown's item, deleting it from the test",
        )
        check(
            f"  {fname} emits exactly one expand step (the combo) plus the Save click",
            content.count("osExpandCollapse(_appHwnd") == 1,
            "a second osExpandCollapse call site would mean the trailing click "
            "was also routed through the dropdown path",
        )
        check(
            f"  {fname} passes the item count so a changed list is refused",
            ", 18)" in content,
            "the helper compares the recorded item count against the live one "
            "and refuses to pick by position when they differ — without it a "
            "reordered/filtered list silently selects the wrong value",
        )


def step_wdio_generate_hwnd_trigger_keeps_name():
    print("\n[14] COM helper target/triggerTarget also reject hwnd-as-automationId (HeidiSQL 더보기 follow-up, 3차)")
    request("DELETE", "/api/events")
    request("POST", "/api/events", HWND_TRIGGER_SESSION_META)
    for ev in HWND_TRIGGER_EVENTS:
        request("POST", "/api/events", ev)

    status, body = request("POST", "/api/generate", {
        "appName": HWND_TRIGGER_APP,
        "platform": PLATFORM,
    }, timeout=30)
    check("POST /api/generate (hwnd trigger) returns 200", status == 200, f"got {status}")
    if status != 200:
        check("(skipped hwnd-trigger checks)", False, body.get("message", ""))
        return
    files = body.get("files", [])
    for f in files:
        fname = f.get("filename", "")
        content = f.get("content", "")
        check(
            f"  {fname} never embeds the trigger's own hwnd as its automationId",
            '"automationId":"9988776"' not in content,
            "the trigger's automationId equals its own NativeWindowHandle — "
            "reassigned every launch, so embedding it verbatim in "
            "triggerTarget guarantees 'trigger not found' at replay "
            "(HeidiSQL 더보기 SplitButton, 2026-07-29)",
        )
        check(
            f"  {fname} keeps the trigger's Name once its hwnd-id is rejected",
            'osScopedInvoke(_appHwnd, {"automationId":"","className":"","name":"Prefs"}, {"automationId":"","className":"SplitButton","name":"More"}, null, null, "Calculator");' in content,
            "dropping the Name too (the old 'automationId present -> drop "
            "Name' rule, applied even to a rejected hwnd-id) leaves the "
            "trigger with NO usable field at all — Name must survive when "
            "the automationId it would have deferred to turns out unstable",
        )


def step_wdio_generate_dup_dropdown_position_disambiguation():
    print("\n[15] Same-window reused automationId=\"DropDown\" routed through COM with a position hint (HeidiSQL follow-up, 4차)")
    request("DELETE", "/api/events")
    request("POST", "/api/events", DUP_DROPDOWN_SESSION_META)
    for ev in DUP_DROPDOWN_EVENTS:
        request("POST", "/api/events", ev)

    status, body = request("POST", "/api/generate", {
        "appName": DUP_DROPDOWN_APP,
        "platform": PLATFORM,
    }, timeout=30)
    check("POST /api/generate (dup dropdown) returns 200", status == 200, f"got {status}")
    if status != 200:
        check("(skipped dup-dropdown checks)", False, body.get("message", ""))
        return
    files = body.get("files", [])
    for f in files:
        fname = f.get("filename", "")
        content = f.get("content", "")
        check(
            f"  {fname} routes a same-window DropDown click through COM instead of WAD's ambiguous accessibility-id lookup",
            "'~DropDown'" not in content,
            "WAD's 'accessibility id' search has no way to disambiguate two "
            "controls sharing automationId=\"DropDown\" in the same window — "
            "it must go through osScopedInvoke's position-aware COM search "
            "instead (HeidiSQL 새 세션 dialog, 2026-07-29)",
        )
        check(
            f"  {fname} embeds each DropDown click's own recorded relY as a disambiguation hint",
            'osScopedInvoke(_appHwnd, {"automationId":"DropDown","className":"","name":""}, null, 84);' in content
            and 'osScopedInvoke(_appHwnd, {"automationId":"DropDown","className":"","name":""}, null, 112);' in content,
            "each DropDown click must carry ITS OWN captured relY — reusing "
            "the same target/hint for both would defeat the whole point of "
            "distinguishing them",
        )


def step_wdio_generate_combobox_ex_reclick_drops_name():
    print("\n[17] TComboBoxEx re-click never uses its state-dependent Name (HeidiSQL 네트워크 유형, 2026-07-31)")
    request("DELETE", "/api/events")
    request("POST", "/api/events", COMBOBOXEX_RECLICK_SESSION_META)
    for ev in COMBOBOXEX_RECLICK_EVENTS:
        request("POST", "/api/events", ev)

    status, body = request("POST", "/api/generate", {
        "appName": COMBOBOXEX_RECLICK_APP,
        "platform": PLATFORM,
    }, timeout=30)
    check("POST /api/generate (ComboBoxEx re-click) returns 200", status == 200, f"got {status}")
    if status != 200:
        check("(skipped ComboBoxEx re-click checks)", False, body.get("message", ""))
        return
    for f in body.get("files", []):
        fname = f.get("filename", "")
        content = f.get("content", "")
        check(
            f"  {fname} never builds a selector from the combo's currently-selected-value Name",
            '"MariaDB or MySQL (SSH tunnel)"' not in content,
            "TComboBoxEx's own Name IS whatever value is currently selected — "
            "a selector using it can only match AFTER that value has already "
            "been chosen, never at replay start. Real failure measured live: "
            "click-not-found://Pane[@ClassName=\"TComboBoxEx\" and "
            "@Name=\"MariaDB or MySQL (SSH tunnel)\"]",
        )
        check(
            f"  {fname} routes the click through COM with className-only + position hint",
            'osScopedInvoke(_appHwnd, {"automationId":"","className":"TComboBoxEx","name":""}, null, 84);' in content,
            "with Name force-dropped and automationId absent, className is "
            "the only remaining field — must still reach a valid, complete "
            "osScopedInvoke() call carrying the recorded relY, not an empty "
            "or malformed target",
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
def step_com_sendinput_helpers():
    """COM 예외 구간의 시각적 클릭 재생 (2026-07-24, dynamic ClickablePoint + SendInput).

    WAD가 붙지 못하는 owned 다이얼로그/네이티브 팝업에서 순수 COM
    InvokePattern.Invoke()는 커서 이동도 눌림 효과도 없어 "사람이 보면서
    재생을 확인할 수 있어야 한다"(§6)를 구조적으로 못 채웠다. send_input_click()이
    그 구간 앞에 붙되, (a) 기존 프로그래매틱 폴백을 대체하지 않고, (b) WAD가
    담당하는 메인 경로는 건드리지 않는다는 것이 이 체크의 요지.
    """
    print("\n[11] COM-exception clicks replay visibly (dynamic ClickablePoint + SendInput)")
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    # 2026-07-27: these two scripts moved from the app's own output folder to
    # generated-wdio/_debug-helpers/<App>/ (saveFiles() split) — they're no
    # longer read by the generated .js at all, just kept here for inspection.
    out_dir = os.path.join(repo_root, "generated-wdio", "_debug-helpers", APP_NAME)

    for py_name in ("osScopedInvoke.py", "osExpandCollapse.py"):
        path = os.path.join(out_dir, py_name)
        if not os.path.exists(path):
            check(f"  {py_name} generated", False, f"missing at {path}")
            continue
        with open(path, encoding="utf-8") as fh:
            src = fh.read()

        try:
            py_compile.compile(path, doraise=True)
            check(f"  {py_name} compiles", True)
        except Exception as e:
            check(f"  {py_name} compiles", False, str(e))

        # 정의와 호출부를 각각 확인한다 — 2026-07-16 버그 D(호출부만 있고
        # SESSION_HEADER에 정의가 없어 ReferenceError)의 교훈.
        check(
            f"  {py_name} defines send_input_click()",
            "def send_input_click(" in src,
            "the shared COM_INPUT_PY snippet was not interpolated into this template",
        )
        check(
            f"  {py_name} calls send_input_click() from invoke_item()",
            "if send_input_click(uia, el," in src,
            "the helper is defined but never wired into the click path — clicks "
            "in the COM exception window would stay invisible (§6)",
        )
        check(
            f"  {py_name} injects real input (SendInput + dynamic ClickablePoint)",
            "SendInput" in src and "GetClickablePoint" in src,
            "visible replay requires actual input injection at the point UIA "
            "computes at runtime, not a programmatic pattern call",
        )
        check(
            f"  {py_name} raises DPI awareness before resolving points",
            "SetProcessDpiAwarenessContext" in src and "enable_per_monitor_dpi()" in src,
            "a DPI-unaware python process gets virtualized UIA rects while "
            "SendInput absolute coords are physical pixels — the two disagree "
            "on any scaled display (agent.py:_enable_per_monitor_dpi_awareness)",
        )
        check(
            f"  {py_name} verifies the point belongs to the target before injecting",
            "WindowFromPoint" in src and "ElementFromPoint" in src,
            "without the hit-test round-trip, a covered/stale point clicks "
            "whatever happens to be there — exactly the 2026-07-15 accident "
            "(clicked the user's own Explorer window and reported success)",
        )
        check(
            f"  {py_name} keeps the programmatic fallback chain intact",
            "IUIAutomationInvokePattern" in src
            and "IUIAutomationSelectionItemPattern" in src
            and "IUIAutomationLegacyIAccessiblePattern" in src,
            "SendInput must sit IN FRONT OF the existing chain, not replace it "
            "— when the safety checks fail the step must still work, just "
            "invisibly (2026-07-24 stakeholder instruction)",
        )
        check(
            f"  {py_name} tags the exception path in the execution log",
            "[COM-SendInput]" in src,
            "runs that left the WAD boundary must be traceable in the log",
        )
        # 2026-07-24 FileZilla 실측: 메뉴 항목/다이얼로그 버튼은 클릭 즉시
        # 파괴돼, 주입 후에 Name을 읽으면 로그가 전부 '?'로 남는다.
        label_at = src.find("label = el.CurrentName")
        send_at = src.find("MOUSEEVENTF_LEFTDOWN | MOUSEEVENTF_ABSOLUTE")
        check(
            f"  {py_name} reads the log label BEFORE injecting input",
            label_at != -1 and send_at != -1 and label_at < send_at,
            "the clicked element is often destroyed by its own click (menu "
            "item, dialog button) — reading its Name afterwards logs '?' and "
            "destroys the traceability the exception path exists to provide",
        )

    ec_path = os.path.join(out_dir, "osExpandCollapse.py")
    if os.path.exists(ec_path):
        with open(ec_path, encoding="utf-8") as fh:
            ec_src = fh.read()
        check(
            "  osExpandCollapse.py retries resolve_target() for a slow post-navigation render",
            "for attempt in range(10):" in ec_src
            and "target = resolve_target(uia, root, sel)" in ec_src,
            "measured 2026-08-04 (HeidiSQL '환경 설정' -> '파일 및 탭' tab switch): "
            "the tab-switch click reports success in a separate process before "
            "this process's search runs, but the new tab's controls (e.g. "
            "TComboBox) had not reached the UIA tree yet — a single-shot "
            "resolve_target() failed every time with 'target element not "
            "found'. osScopedInvoke.py already carries this exact retry budget "
            "for the same class of render race (2026-07-17/24); this helper "
            "never got it",
        )

    path = os.path.join(out_dir, "osScopedInvoke.py")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            src = fh.read()
        check(
            "  osScopedInvoke.py disambiguates a reused trigger AutomationId",
            "def pick_trigger(" in src and "FindAll" in src
            and "pick_trigger(uia, root, trigger_cond, win_top, args.trigger_rel_y)" in src,
            "PuTTY gives every ComboBox dropdown arrow the same "
            "automationId='DropDown' and its Name is state-dependent (dropped "
            "by the 2026-07-14 guard), so FindFirst always re-opened the FIRST "
            "combo — two Proxy-panel steps failed while the log showed the "
            "same coordinates every time (2026-07-24)",
        )
        check(
            "  osScopedInvoke.py retry budget covers a slow inline rename box",
            "attempts = 20 if args.text_b64 else 10" in src,
            "measured 2026-07-24 (poc/diag_filezilla_rename.py): FileZilla's "
            "inline rename box appears 2260ms after the '새 사이트(N)' click, so "
            "the old 4-attempt (~0.9s) budget could never see it; typing now "
            "waits ~6s while clicks stay at ~2.7s",
        )
        check(
            "  osScopedInvoke.py falls back to the focused input when typing",
            "def focused_input(" in src and "focused_input(uia, main_pid.value)" in src,
            "the rename box is captured with automationId='1' but exposes an "
            "EMPTY automationId at replay time (measured) — no selector can "
            "ever match it; it always holds keyboard focus, which is the only "
            "stable, coordinate-free handle on it",
        )
        check(
            "  osScopedInvoke.py restricts that fallback to typing and to our PID",
            "if args.text_b64:\n        el = focused_input" in src
            and "el.CurrentProcessId != main_pid" in src,
            "clicking 'whatever has focus' would silently perform the wrong "
            "action, and typing into another process's focused control would "
            "leak keystrokes out of the app under test",
        )


def step_esc_recovery_guards():
    """ESC 복구가 스스로 재시도를 망치지 않아야 한다 (2026-07-24).

    FileZilla 인라인 이름변경 상자에서 ESC는 이름변경 자체를 취소하므로,
    type 스텝의 Fail-and-Recover가 ESC를 보내면 2차 시도는 실패가 보장된다.
    또한 SESSION_HEADER의 _step()은 2026-07-14 RC-C 수정(전경 창 가드)이
    SIMPLE_HEADER에만 적용돼 무조건 osActivate+ESC를 보내고 있었다.
    """
    print("\n[12] _step() ESC recovery guards (type steps / session-mode parity)")
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    targets = [
        (APP_NAME, f"{APP_NAME}TestById.js", "simple"),
        (SESSION_APP, f"{SESSION_APP}TestById.js", "session"),
    ]
    for app, js_name, mode in targets:
        path = os.path.join(repo_root, "generated-wdio", app, js_name)
        if not os.path.exists(path):
            check(f"  {js_name} generated", False, f"missing at {path}")
            continue
        with open(path, encoding="utf-8") as fh:
            js = fh.read()
        # 정의부와 호출부를 각각 확인 — 2026-07-16 버그 D 교훈
        check(
            f"  [{mode}] defines _escWouldHarm()",
            "function _escWouldHarm(label)" in js,
            "the shared preamble helper is missing from this header",
        )
        check(
            f"  [{mode}] _step() actually consults it",
            "_escWouldHarm(label)" in js and "esc-skipped:" in js,
            "defining the guard without calling it leaves the ESC that "
            "cancels an inline rename in place",
        )
        check(
            f"  [{mode}] never sends ESC while our own window is foreground",
            "esc-skipped-main-foreground:" in js,
            "an unconditional ESC on a dialog-based main window (PuTTY "
            "Configuration) means ESC == Cancel == app closed; the session "
            "header kept doing this until 2026-07-24",
        )
    session_js = os.path.join(repo_root, "generated-wdio", SESSION_APP,
                              f"{SESSION_APP}TestById.js")
    if os.path.exists(session_js):
        with open(session_js, encoding="utf-8") as fh:
            js = fh.read()
        check(
            "  [session] no longer force-activates the main window before ESC",
            "osActivate('', _hwndCache[_mainTitleFrag]);\n        osEscape();" not in js,
            "raising the main dialog to the foreground and THEN sending ESC is "
            "exactly what closed PuTTY on every failed step (2026-07-14 RC-C)",
        )

def step_wad_boundary_intact():
    """WAD-primary 경계는 그대로여야 한다 — SendInput은 COM 구간 전용이다."""
    print("\n[13] WAD-primary boundary unchanged by the COM input path")
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out_dir = os.path.join(repo_root, "generated-wdio", APP_NAME)
    for js_name in (f"{APP_NAME}TestById.js", f"{APP_NAME}TestByClass.js"):
        path = os.path.join(out_dir, js_name)
        if not os.path.exists(path):
            check(f"  {js_name} generated", False, f"missing at {path}")
            continue
        with open(path, encoding="utf-8") as fh:
            js = fh.read()
        js = _strip_embedded_helpers(js)
        check(
            f"  {js_name} still clicks through WinAppDriver (WAD-primary intact)",
            "/element/" in js and "/click" in js,
            "the main-window path must keep using WAD element/click — COM is a "
            "narrow exception, never a replacement engine (server.js boundary "
            "comment, 2026-07-24)",
        )
        check(
            f"  {js_name} contains no input injection of its own",
            "SendInput" not in js and "mouse_event" not in js,
            "input emulation belongs only in the COM helper scripts",
        )
        check(
            f"  {js_name} passes no static coordinates to the COM helpers",
            "--x " not in js and "--y " not in js and "'--x'" not in js,
            "the redefined §3 rule still forbids recorded/static coordinates — "
            "points must be computed at replay time from the resolved element",
        )
        # 2026-07-24 Calculator: 세션이 죽은 뒤에도 남은 스텝마다 20초씩
        # 기다리느라 4분을 더 태웠다(같은 시점 독립 COM은 46ms에 응답).
        check(
            f"  {js_name} stops waiting once the session is provably dead",
            "_sessionDead" in js and "session-unresponsive:" in js
            and "_SESSION_DEAD_AFTER" in js,
            "consecutive 20s timeouts mean the driver stopped answering; "
            "continuing to poll it just buries the real failure under minutes "
            "of dead waiting",
        )


GOLDEN_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "golden")


def _normalize_generated(text):
    return text.replace("\r\n", "\n")


def _print_diff_head(expected_text, actual_text, label, max_lines=30):
    import difflib
    diff = difflib.unified_diff(
        expected_text.splitlines(keepends=True),
        actual_text.splitlines(keepends=True),
        fromfile=f"expected/{label}", tofile=f"actual/{label}", n=2,
    )
    lines = list(diff)[:max_lines]
    print(f"    --- diff ({label}, first {max_lines} lines) ---")
    for line in lines:
        print("    " + line.rstrip("\n"))
    print("    ---")


def step_golden_recordings():
    """골든 레코딩 회귀 게이트 (2026-08-05).

    지금까지의 회귀(예: FileZilla 도움말/HeidiSQL 더보기의 osExpandCollapse
    인덱스 무시 버그)는 전부 "특정 앱 실측에서 나온 좁은 규칙이 대리 조건으로
    인해 다른 앱까지 잘못 포획"하는 패턴이었다. mock_events.py의 나머지
    시나리오는 합성 이벤트만 다뤄서 이런 교차-앱 영향을 못 잡는다 —
    server.js를 고칠 때마다 실제 검증된 6개 앱 녹화(agent/golden/recordings/)
    전부를 /api/generate에 통과시켜, 생성된 JS가 골든 파일(agent/golden/
    expected/)과 바이트 단위로 일치하는지 비교한다. 의도한 변경이면
    `UPDATE_GOLDEN=1 python agent/mock_events.py`로 재축복(re-bless)한다.

    appName은 실제 프리셋 이름(FileZilla 등)을 절대 쓰지 않는다 — 이 게이트가
    쓰는 exePath는 골든 녹화 시점의 것이라 최신 실제 결과와 다를 수 있고,
    같은 appName을 쓰면 generated-wdio/<실제앱>/을 이 스크립트가 덮어써서
    사용자가 방금 검증한 진짜 결과물을 파괴한다(APP_NAME 위 주석, 2026-07-24
    Calculator 사고와 동일 클래스의 문제) — 그래서 MockGolden<App> 접두사를
    쓰고, .gitignore/step_output_folders_isolated()에도 그 이름으로 등록한다.
    """
    print("\n[14] Golden recordings — generated JS matches known-good output")
    manifest_path = os.path.join(GOLDEN_DIR, "manifest.json")
    if not os.path.exists(manifest_path):
        check("  golden manifest present", False, f"missing: {manifest_path}")
        return
    with open(manifest_path, encoding="utf-8") as fh:
        manifest = json.load(fh)

    bless = os.environ.get("UPDATE_GOLDEN") == "1"
    first_response_text = None
    for entry in manifest:
        app = entry["app"]
        rec_path = os.path.join(GOLDEN_DIR, "recordings", entry["recording"])
        if not os.path.exists(rec_path):
            check(f"  golden[{app}] recording present", False, f"missing: {rec_path}")
            continue
        with open(rec_path, encoding="utf-8") as fh:
            events = json.load(fh)

        request("DELETE", "/api/events")
        for ev in events:  # events[0] is the session_meta object, posted like any other
            request("POST", "/api/events", ev)
        status, body = request(
            "POST", "/api/generate",
            {"appName": entry["appName"], "exePath": entry["exePath"],
             "platform": entry["platform"]},
            timeout=60,
        )
        if status != 200 or not body.get("ok"):
            check(f"  golden[{app}] /api/generate ok", False,
                  f"status={status} body={str(body)[:200]}")
            continue
        check(f"  golden[{app}] /api/generate ok", True)

        files = body.get("files", [])
        exp_dir = os.path.join(GOLDEN_DIR, "expected", app)
        os.makedirs(exp_dir, exist_ok=True)
        for f in files:
            actual = _normalize_generated(f["content"])
            exp_path = os.path.join(exp_dir, f["filename"])
            if bless:
                with open(exp_path, "w", encoding="utf-8", newline="\n") as fh:
                    fh.write(actual)
                check(f"  golden[{app}] {f['filename']} blessed", True)
                continue
            if not os.path.exists(exp_path):
                check(f"  golden[{app}] {f['filename']} matches golden", False,
                      f"no golden file yet — run with UPDATE_GOLDEN=1 first: {exp_path}")
                continue
            with open(exp_path, encoding="utf-8") as fh:
                expected = _normalize_generated(fh.read())
            ok = actual == expected
            if not ok:
                _print_diff_head(expected, actual, f"{app}/{f['filename']}")
            check(f"  golden[{app}] {f['filename']} matches golden", ok)

        if first_response_text is None and files:
            first_response_text = (entry, events)

    # 결정성 사전 체크: 같은 픽스처를 두 번 generate 했을 때 응답이 같아야
    # 한다 — 여기서 안 잡히면 골든 비교 자체가 타임스탬프/난수 오염으로
    # 상시 깨지는 시나리오가 된다.
    if first_response_text:
        entry, events = first_response_text
        request("DELETE", "/api/events")
        for ev in events:
            request("POST", "/api/events", ev)
        status2, body2 = request(
            "POST", "/api/generate",
            {"appName": entry["appName"], "exePath": entry["exePath"],
             "platform": entry["platform"]},
            timeout=60,
        )
        files2 = {f["filename"]: _normalize_generated(f["content"])
                  for f in body2.get("files", [])} if status2 == 200 else {}
        status3, body3 = request(
            "POST", "/api/generate",
            {"appName": entry["appName"], "exePath": entry["exePath"],
             "platform": entry["platform"]},
            timeout=60,
        )
        files3 = {f["filename"]: _normalize_generated(f["content"])
                  for f in body3.get("files", [])} if status3 == 200 else {}
        check(
            "  golden generate is deterministic (same fixture -> byte-identical output twice)",
            files2 and files2 == files3,
            "if this fails, the golden comparison above is unreliable regardless "
            "of whether individual files matched",
        )


def step_output_folders_isolated():
    """Every folder this gate generates into must be gitignored.

    The gate calls /api/generate without an exePath, so its output folder is
    overwritten with a synthetic, unrunnable build on every run. A real
    recording preset's folder (generated-wdio/Calculator, .../FileZilla, ...)
    is tracked in git and holds a real capture — if a scenario here ever
    targets one, that capture is silently destroyed (happened on 2026-07-24
    via APP_NAME = "Calculator"). "Is it gitignored?" is the cheap, durable
    proxy for "is this folder mine to clobber?".
    """
    print("\n[0] Mock output folders are isolated from real recording presets")
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ignored = set()
    with open(os.path.join(repo_root, ".gitignore"), encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line.startswith("generated-wdio/"):
                ignored.add(line[len("generated-wdio/"):].rstrip("/"))

    targets = sorted({
        APP_NAME, SESSION_APP, COLLISION_APP, DELAYED_HWND_APP,
        EXPAND_REDUNDANT_APP, NATIVE_APP, VCL_APP, TRIGGER_EXPAND_APP,
        NAMELESS_ITEM_APP, HWND_TRIGGER_APP, DUP_DROPDOWN_APP, ANIM_APP,
        NESTED_DROPDOWN_APP, SIMPLE_ROOTHWND_APP, TITLE_COLLISION_DIALOGRECT_APP,
        WEB_APP, DBLROW_APP, WINCLICK_APP, VOLATILE_MENUITEM_APP,
        "SevenZipStateReset",
        "MockGoldenCalculator", "MockGoldenFileZilla", "MockGoldenHeidiSQL",
        "MockGoldenPuTTY", "MockGoldenSevenZip", "MockGoldenTeamViewer",
    })
    for name in targets:
        check(
            f"  generated-wdio/{name}/ is gitignored (safe to clobber)",
            name in ignored,
            "this scenario writes an exePath-less synthetic build into a "
            "folder git tracks — if it is a real recording preset, running "
            "this gate destroys that capture",
        )


def main():
    print("=" * 54)
    print("  mock_events.py - QAForge pipeline regression test")
    print("=" * 54)
    print(f"  Target: {BASE}")

    step_output_folders_isolated()
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
    step_wdio_generate_simple_roothwnd()
    step_wdio_generate_title_collision_dialogrect()
    step_wdio_generate_session()
    step_wdio_generate_window_collision()
    step_wdio_generate_delayed_hwnd()
    step_wdio_generate_expand_redundant_trigger()
    step_wdio_generate_postnav_title_keeps_trigger_window()
    step_wdio_generate_native()
    step_wdio_generate_vcl_hwnd_id()
    step_wdio_generate_web_content()
    step_wdio_generate_doubleclick_row()
    step_wdio_generate_window_click()
    step_wdio_generate_trigger_expand_merge_order()
    step_wdio_generate_nameless_item_no_fake_itemname()
    step_wdio_generate_owner_drawn_dropdown_by_index()
    step_wdio_generate_combobox_ex_reclick_drops_name()
    step_wdio_generate_hwnd_trigger_keeps_name()
    step_wdio_generate_volatile_menuitem_id()
    step_wdio_generate_dup_dropdown_position_disambiguation()
    step_com_sendinput_helpers()
    step_esc_recovery_guards()
    step_wad_boundary_intact()
    step_golden_recordings()

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
