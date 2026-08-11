"""
AI-Powered Desktop Automation Code Generator - Python Capture Agent
====================================================================
- Launches the target .exe and hooks global mouse/keyboard input (pynput)
- Reads element details from Windows UI Automation (comtypes)
- Filters events to the target application by TOP-LEVEL WINDOW HANDLE
  (window under the pointer / foreground window == the launched app's window).
  This is locale-independent and works for Win32 AND UWP (where the classic
  hwnd->process->exe chain breaks: WindowFromPoint returns the
  ApplicationFrameWindow whose GA_ROOT still equals the tracked window).
- Buffers keystrokes into single `type` events
- Detects double-clicks, debounces scrolls
- POSTs each captured event to the Express bridge (port 3002)
- Exposes a small HTTP control server on port 4444 (/start, /stop, /status)

MUST be run from an *Administrator* terminal, otherwise UIA properties
(AutomationId, Name, ...) come back empty for most applications.

    pip install -r requirements.txt
    python agent.py
"""

import hashlib
import json
import math
import os
import queue
import re
import subprocess
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import requests
from pynput import keyboard, mouse

# Windows-only imports
import ctypes
from ctypes import wintypes

import win32gui
import win32process

# ----------------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------------
AGENT_PORT = 4444
EXPRESS_EVENTS_URL = "http://localhost:3002/api/events"

DOUBLE_CLICK_INTERVAL = 0.50   # seconds
DOUBLE_CLICK_RADIUS = 6        # pixels
# 2026-08-11 (FileZilla "새 북마크" 체크박스 실측, STEP27): how long a failed
# snapshot_open_menu() (menu-close/dialog-open capture race) stays "fresh"
# enough to flag the NEXT self-healed click as ambiguous. The observed lag
# was 1.4-1.5s (worker-thread inspection catching up after the OS had already
# closed the menu and opened the dialog); this must expire quickly or a
# single stale failure would mislabel every unrelated click for the rest of
# the recording (2026-08-11 review feedback) — not a session-wide counter.
AMBIGUOUS_SELF_HEAL_WINDOW_S = 2.0   # seconds
DRAG_MIN_DIST = 10             # pixels — press-to-release distance above which
                                # a left click is recorded as a drag instead
                                # (deliberately above DOUBLE_CLICK_RADIUS so a
                                # normal double-click's small jitter never
                                # misfires as a drag)
SCROLL_FLUSH_IDLE = 0.40       # seconds of no scrolling -> emit scroll event
QUEUE_POLL_TIMEOUT = 0.20      # worker wakeup interval for pending flushes
DISCOVER_TIMEOUT = 5.0         # seconds to wait for the target window to appear
# 2026-08-10 (FileZilla '..' 단일 클릭 실측): 로컬 파일목록/트리 행에서 이름
# 없는(또는 automationId/className 둘 다 없는) ListItem/TreeItem을 단일
# 클릭할 때, 그 클릭이 실제로 뷰를 바꿨는지(선택이 아니라 네비게이션/펼치기)
# 이 지연 뒤에 확인한다 — 7번 이슈에서 측정한 형제-개수 수렴 시간(0.6~0.9s)과
# 같은 예산.
ACTIVATION_CHECK_DELAY = 0.40  # seconds before verifying a candidate single click
UIA_EXPAND_COLLAPSE_PATTERN_ID = 10005   # same value as UIAInspector.EXPAND_COLLAPSE_PATTERN_ID

UIA_CONTROL_TYPES = {
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
# Control types treated as text-input fields (used only to set the
# isInputField flag — NOT used to drop keystrokes).
INPUT_CONTROL_TYPES = {"Edit", "Document", "ComboBox"}

# Numpad virtual-key codes -> character. Recovers numpad digits/operators even
# when pynput reports them without a .char (e.g. NumLock off makes numpad 8 a
# navigation key). VK_NUMPAD0..9 = 96..105, then operators.
NUMPAD_VK = {
    96: "0", 97: "1", 98: "2", 99: "3", 100: "4", 101: "5", 102: "6",
    103: "7", 104: "8", 105: "9",
    106: "*", 107: "+", 109: "-", 110: ".", 111: "/",
}

GA_ROOT = 2                    # GetAncestor flag


def log(*args):
    print("[agent]", *args, flush=True)


def point_in_rect(rect, x, y):
    """Win32/UIA rect semantics: `right` and `bottom` are EXCLUSIVE.

    A pixel at y == rect.bottom is one row BELOW the control, not its last
    row. Testing containment with `<=` therefore adopts clicks that physically
    missed the target — measured 2026-08-03 on a TeamViewer login dialog:

        click pt=(916,533)  비밀번호 Edit rect=[822,517,1022,533]  -> y == bottom
            captured as "click 비밀번호", but the real click landed outside the
            field, focus stayed on the 이메일 Edit above it, and the keystrokes
            that followed were (correctly) attributed to 이메일.
        click pt=(852,532)  same rect                              -> inside
            focus moved, and the next keystrokes went to 비밀번호.

    One pixel decided it, which is what rules out a focus/timing race and
    pins the fault on this comparison. Replaying the fabricated event is
    worse than dropping it: UIA clicks the element's CENTRE, so replay DOES
    focus the field and then diverges from the recording it came from.
    """
    if not isinstance(rect, (tuple, list)) or len(rect) != 4:
        return False
    left, top, right, bottom = rect
    return left <= x < right and top <= y < bottom


def rects_close(r1, r2, tol=2):
    """True when two (left, top, right, bottom) rects describe the same
    on-screen position within `tol` pixels per edge — a looser stand-in for
    `==` when comparing a rect read at two different moments in time (e.g.
    identity-rot recovery, 2026-08-10). A strict `==` can be fooled by a
    scrollbar appearing/disappearing or similar sub-pixel repaint jitter
    between the two reads, which would wrongly block a recovery that should
    fire."""
    if not (isinstance(r1, tuple) and len(r1) == 4
            and isinstance(r2, tuple) and len(r2) == 4):
        return False
    return all(abs(a - b) <= tol for a, b in zip(r1, r2))


def is_exclusive_edge_miss(rect, x, y):
    """True when (x, y) sits EXACTLY on `rect`'s right or bottom edge.

    That is: inside under inclusive bounds, outside under the exclusive ones
    point_in_rect() uses. This is the genuinely ambiguous case — measured
    twice, with opposite ground truths and identical geometry:

        TeamViewer 2026-08-03  비밀번호 Edit rect=[822,517,1022,533] click y=533
            -> physically MISSED (focus stayed on the 이메일 Edit above)
        FileZilla  2026-08-05  파일(F) rect=(396,84,461,108)  click y=108
            -> physically HIT (the menu opened: the watcher registered the
               popup window and the open-menu scan counted its 8 MenuItems)

    Nothing in the rect distinguishes them, so point_in_rect() keeps
    rejecting both — replaying a click the recording never actually made is
    the worse failure (see its docstring). What this predicate is for is the
    COST of that rejection, not the rejection itself: an edge miss is never
    an open dropdown/menu item sitting below its trigger (that lands tens of
    pixels away, not on the boundary pixel) and never a light-dismiss scrim,
    so all of those recovery searches are guaranteed to fail. Skipping them
    keeps one dropped click from cascading into a broken recording — measured
    2026-08-05: those searches cost 2.4s on this one click, which put the
    worker thread permanently behind, and every later menu snapshot then ran
    too late to see its menu, losing menuItemIndex for the whole session.
    """
    if not isinstance(rect, (tuple, list)) or len(rect) != 4:
        return False
    left, top, right, bottom = rect
    return (left <= x <= right and top <= y <= bottom
            and not point_in_rect(rect, x, y))


# Window classes an embedded Chromium view publishes. Detection is by window
# class only — never by app name or exe path — so WebView2, Electron and CEF
# are all covered by the same rule (CLAUDE.md §6: no per-app integration).
# Measured 2026-08-03, TeamViewer 15.79 child chain:
#   MainWindowOne > TV_WebView2Control > Chrome_WidgetWin_0/1
#                                      > Chrome_RenderWidgetHostHWND
CHROMIUM_HOST_CLASSES = (
    "Chrome_WidgetWin",
    "Chrome_RenderWidgetHostHWND",
    "TV_WebView2Control",
)


def is_chromium_host_class(class_name):
    """True when a window class name belongs to embedded Chromium.

    Split out from is_web_host() so the matching rule is testable without a
    live UI: is_web_host() answers "is any Chromium window under this hwnd",
    which depends on what happens to be running on the machine and cannot be
    asserted deterministically.
    """
    return bool(class_name) and any(
        class_name.startswith(c) for c in CHROMIUM_HOST_CLASSES)


def is_web_host(hwnd):
    """True when `hwnd` has a descendant window belonging to embedded Chromium.

    Such an app publishes its accessibility tree progressively, so both
    capture and replay must wait for it to settle (see settled_subtree_count).
    """
    if not hwnd:
        return False
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def cb(child, _):
        try:
            buf = ctypes.create_unicode_buffer(256)
            ctypes.windll.user32.GetClassNameW(child, buf, 256)
            if is_chromium_host_class(buf.value):
                found.append(child)
                return False
        except Exception:
            pass
        return True

    try:
        ctypes.windll.user32.EnumChildWindows(hwnd, cb, 0)
    except Exception:
        return False
    return bool(found)


def smallest_rect_index(rects, x, y):
    """Index of the smallest rect containing (x, y), or None.

    Split out from the UIA walk so the selection rule is testable without a
    live UI. Ties go to the first rect, which is the tree order UIA returned.
    """
    best_i, best_area = None, None
    for i, r in enumerate(rects):
        if not point_in_rect(r, x, y):
            continue
        area = max(0, r[2] - r[0]) * max(0, r[3] - r[1])
        if best_area is None or area < best_area:
            best_i, best_area = i, area
    return best_i


# ----------------------------------------------------------------------------
# UI Automation helpers (run ONLY on the worker thread - COM apartment there)
# ----------------------------------------------------------------------------
class UIAInspector:
    """Thin wrapper around the raw IUIAutomation COM interface."""

    def __init__(self):
        import comtypes
        import comtypes.client
        comtypes.CoInitialize()
        # Generates/loads the UIAutomationClient wrapper module. Kept on the
        # instance because pattern QueryInterface calls need its interface
        # types (e.g. IUIAutomationExpandCollapsePattern).
        self._mod = comtypes.client.GetModule("UIAutomationCore.dll")
        self._uia = comtypes.client.CreateObject(
            "{ff48dba4-60ef-4201-aa87-54103eef594e}",
            interface=self._mod.IUIAutomation,
        )
        # Geometry of the most recently observed OPEN dropdown list —
        # see snapshot_open_dropdown(). One inspector lives for the whole
        # worker loop, so this survives between events.
        self._dropdown_cache = None
        # Same as _dropdown_cache, for owner-drawn popup menu items (HeidiSQL
        # "더 보기") — see snapshot_open_menu()/menu_item_self(). Kept
        # separate: a combo and a popup menu are never legitimately open at
        # once, but sharing one cache would still couple two independent
        # state machines for no reason.
        self._menu_cache = None
        # Timestamp of the most recent snapshot_open_menu() failure ("Expanded
        # but no Menu container with items was found") — a signal that the
        # popup-menu detection lost a timing race. Read (and expired after
        # AMBIGUOUS_SELF_HEAL_WINDOW_S) by _inspect()'s self-heal branch to
        # flag a suspiciously-timed click instead of silently trusting it —
        # see ambiguousCapture below. Must expire quickly: without a TTL, one
        # stale failure would mislabel every unrelated click for the rest of
        # the session (2026-08-11 review feedback).
        self._menu_snapshot_fail_ts = None
        # DIAGNOSTIC: element_at()'s decision path for the most recent call,
        # read back by _inspect() to emit one [trace] line. Observation
        # only — nothing downstream reads this dict, so it cannot change
        # behavior. Exists because [diag-click] only ever showed the FINAL
        # adopted element, which looks identical whether it came from a
        # correct hit-test or from a wrong-window fallback — measured
        # 2026-08-04 (PuTTY): this tool's own control-panel elements
        # ("Captured Events (N)", the React root div) were adopted with no
        # log line distinguishing how they were reached, so three
        # consecutive fix attempts couldn't be told apart from a live log.
        self._last_trace = {}

    # File-list rows (e.g. the "폴더 열기" dialog's Explorer ListView) hit-test
    # to this generic in-place-rename edit surrogate rather than the row
    # itself — its Name is the localized COLUMN header ("이름"), not the
    # actual filename/folder name. The row's real Name (e.g. "run",
    # "hansung") lives on the ListItem/TreeItem ancestor (confirmed
    # 2026-07-08: VSCode folder-picker replay opened the wrong folder
    # because every row click fell back to blind rel-offset coordinates —
    # a dialog's last-visited folder/scroll state isn't guaranteed to match
    # between recording and replay, so a coordinate-only click can land on
    # a different row than the one actually clicked).
    GENERIC_CELL_AUTOMATION_IDS = {"System.ItemNameDisplay"}
    ROW_CONTROL_TYPES = {50007, 50024}  # ListItem, TreeItem

    def _nearest_row_ancestor(self, elem, max_up=6):
        """Walk up from elem toward the nearest ListItem/TreeItem ancestor
        that has a real Name. See GENERIC_CELL_AUTOMATION_IDS docstring."""
        try:
            walker = self._uia.ControlViewWalker
            cur = elem
            for _ in range(max_up):
                try:
                    ct = cur.CurrentControlType
                    if ct in self.ROW_CONTROL_TYPES and cur.CurrentName:
                        # 2026-08-05 (7-Zip "hansung" 폴더 진입 실측): 지금 이
                        # 순간 직접 읽어 ROW_CONTROL_TYPES에 있다고 확인한
                        # controlType이다. 이 함수가 반환하는 건 살아있는 COM
                        # 포인터뿐이고, 호출부(_inspect)가 나중에 describe()로
                        # 같은 요소를 다시 읽는다 — 그 사이 요소가 죽으면(자기
                        # 클릭이 유발한 화면 전환 레이스) describe()의 독립된
                        # CurrentControlType 재조회만 조용히 실패해 빈 문자열로
                        # 남고, name은 그보다 먼저 읽혀 성공한 채로 남는다
                        # (describe()가 각 필드를 별도 try/except로 읽으므로).
                        # 그 결과 codegen의 `controlType === 'ListItem'` 분기가
                        # 안 걸려 WAD REST 폴백으로 새고, WAD는 이 컨트롤에서
                        # 에러 없이 끝나면서도 목록을 갱신하지 않는다(바로 이
                        # 클래스의 컨트롤에 대해 이미 실측된 사실 — server.js
                        # ListItem 분기 주석 2026-07-15 참고) — 성공 로그도
                        # 실패 로그도 없이 그냥 아무 일도 안 일어난다. 방금
                        # 확인한 값을 트레이스에 남겨, describe()의 재조회가
                        # 실패해도 이미 확인된 사실을 잃지 않게 한다.
                        if getattr(self, "_last_trace", None) is not None:
                            self._last_trace["confirmedRowControlType"] = (
                                UIA_CONTROL_TYPES.get(ct, str(ct)))
                        return cur
                except Exception:
                    break
                try:
                    parent = walker.GetParentElement(cur)
                except Exception:
                    break
                if parent is None:
                    break
                cur = parent
        except Exception:
            pass
        return None

    def _nearest_named_ancestor(self, elem, max_up=4):
        """Walk up from elem toward the nearest ancestor exposing a usable
        Name or AutomationId — generic fallback for composite controls whose
        hit-tested leaf carries neither (2026-07-29, HeidiSQL '더보기'
        SplitButton: ElementFromPoint sometimes lands on the button's blank
        inner arrow glyph instead of the named outer control — observed
        non-deterministically across otherwise-identical recordings of the
        same click). Unlike _nearest_row_ancestor, not restricted to
        ListItem/TreeItem — this only fires when the leaf already has
        neither id nor name, so any named ancestor is strictly better than
        an unselectable element. Kept shallow (4) to avoid climbing past the
        actual clicked control into an unrelated container."""
        try:
            walker = self._uia.ControlViewWalker
            cur = elem
            for hop in range(max_up):
                try:
                    parent = walker.GetParentElement(cur)
                except Exception as e:
                    log(f"[inspect] _nearest_named_ancestor: GetParentElement raised "
                        f"at hop {hop}/{max_up}: {e}")
                    return None
                # comtypes FindFirst/GetParentElement return a NULL COM pointer
                # on a miss, not None (CLAUDE.md §5) — `is None` never catches
                # it, so the top-of-tree case fell through to
                # parent.CurrentAutomationId on a NULL pointer and raised
                # (measured 2026-08-08, FileZilla SplitButton: "parent
                # property read raised at hop 0/1: NULL COM pointer access").
                # A NULL COM pointer's __bool__ is False, so `not parent`
                # catches both None and NULL without risking a false negative
                # on a valid pointer.
                if not parent:
                    log(f"[inspect] _nearest_named_ancestor: no parent at hop "
                        f"{hop}/{max_up} — reached top of tree")
                    return None
                try:
                    if parent.CurrentAutomationId or parent.CurrentName:
                        return parent
                except Exception as e:
                    log(f"[inspect] _nearest_named_ancestor: parent property read "
                        f"raised at hop {hop}/{max_up}: {e}")
                    return None
                cur = parent
        except Exception as e:
            log(f"[inspect] _nearest_named_ancestor: unexpected failure: {e}")
            return None
        log(f"[inspect] _nearest_named_ancestor: no named ancestor within "
            f"{max_up} hops")
        return None

    def _ancestor_sibling_selector(self, elem, max_up=4, cached_rect=None,
                                    cached_ct=None, cached_name=None):
        """Last-resort structural selector for an elem that is STILL
        nameless after describe()'s LegacyIAccessible/HelpText fallback
        (2026-08-08, FileZilla toolbar buttons — controlType='CheckBox',
        no Name, no AutomationId, and MSAA has nothing either). Finds the
        nearest named ancestor (reusing _nearest_named_ancestor) and this
        element's ordinal position among that ancestor's DIRECT children
        (TreeScope_Children, not Subtree — Subtree's DFS order would let a
        grandchild shift the count for a flat toolbar row) of the SAME
        ControlType. Returns (ancestor_elem, sibling_index, sibling_count)
        or None.

        Does NOT replace elem as the click target — element_at()'s
        elem_was_deepened guard (see its 2026-08-05 comment) already
        rejected doing that, for good reason: swapping in a large named
        container caused replay to click the container's CENTER instead of
        the small precise element (measured on TeamViewer). This only
        builds an auxiliary selector for re-finding elem later; the actual
        click target is unchanged.

        Identity check between elem and a candidate sibling uses
        CurrentBoundingRectangle + ControlType (already matched by the
        FindAll condition) + (CurrentNativeWindowHandle if elem has one,
        else CurrentName) as a tiebreaker — NOT IUIAutomation::CompareElements,
        which this codebase has never called before and which is prone to
        false negatives comparing two separately-queried COM references to
        the same underlying element.

        cached_rect/cached_ct/cached_name (2026-08-08, FileZilla "사이트
        관리자" SplitButton): this is normally called AFTER
        snapshot_open_menu()'s up-to-0.6s retry budget has already run (see
        _inspect()), so by the time it re-queries elem live, real time has
        passed since describe() first read it — measured gap=1.5s on the
        recording that motivated this. describe() already captured a good
        rect/controlType/name near click time; reusing those as the match
        target (instead of re-querying elem) removes that window entirely,
        the same "earliest observation wins" principle _inspect() already
        applies to its dead-element recovery path (see its 2026-08-05
        comment). elem is still needed live for the tree walk
        (GetParentElement/FindAll) — only the "is this candidate the one we
        clicked" comparison switches to the cached values when given.

        Climbs past the FIRST named ancestor if needed (2026-08-08, same
        SplitButton, round 2): the diagnostic log added for the cache fix
        above pinned the real cause — "ancestor has no direct children of
        controlType=50031 (SplitButton)". The FileZilla toolbar's SplitButton
        is nested one wrapper deeper than its sibling CheckBoxes, so the
        NEAREST named ancestor (shared with those CheckBoxes) simply does not
        directly contain it — TreeScope_Children was never going to find it
        there no matter how fresh the query. If the nearest named ancestor's
        direct children don't include a same-controlType match, keep
        climbing to the next named ancestor above it (still within max_up),
        instead of giving up after the first one — a flat toolbar (the
        CheckBox case) still resolves on the first try, unchanged.

        Round 3 (2026-08-08, same SplitButton — the climbing above still came
        up empty at every level): confirmed via poc/probe_ancestor_chain.py
        --walker-vs-refetch, within one COM session — a ControlViewWalker
        reference obtained by climbing UP FROM the SplitButton itself omits
        the SplitButton from its own FindAll(TreeScope_Children) afterward
        (13 children, missing it), while a property-based FindFirst that
        resolves the IDENTICAL ancestor (same hwnd/automationId/rect) sees
        all 14. Climbing from a NEIGHBOR (e.g. a sibling CheckBox) to the
        same ancestor does not trigger it either — only "climbed away from
        the element you're about to search for" does. This is not something
        _nearest_named_ancestor can avoid (it has to start the climb from
        elem); the fix is to never hand a climbed reference to
        _find_sibling_by_controltype directly — re-resolve it by its own
        AutomationId/ClassName/Name first (_refetch_ancestor_clean below).
        This exactly mirrors what server.js's osExpandCollapse.py already
        does at replay time (resolve_target() is a plain property FindFirst,
        never a walker climb) — replay was never affected by this, only
        capture was.
        """
        if (isinstance(cached_rect, tuple) and len(cached_rect) == 4
                and cached_ct is not None):
            ct = cached_ct
            target_rect = cached_rect
            target_name = cached_name or ""
            try:
                target_hwnd = elem.CurrentNativeWindowHandle or 0
            except Exception:
                target_hwnd = 0
        else:
            try:
                ct = elem.CurrentControlType
                r = elem.CurrentBoundingRectangle
                target_rect = (r.left, r.top, r.right, r.bottom)
                target_hwnd = elem.CurrentNativeWindowHandle or 0
                target_name = elem.CurrentName or ""
            except Exception as e:
                log(f"[inspect] _ancestor_sibling_selector: elem re-query raised "
                    f"(no cached rect/controlType available as fallback): {e}")
                return None

        # 2026-08-10 (사용자 설계 제안, TreeView 11초 지연 실측): 레벨당
        # 최대 2.7초(_SIBLING_SETTLE_RETRIES x _SIBLING_SETTLE_INTERVAL)
        # 예산이 조상 climb 단계 수만큼 그대로 곱해지는 게 진짜 문제였다
        # (트리뷰는 들여쓰기 때문에 rect가 레벨마다 미세하게 달라 패치(A)의
        # "정확 일치 시 즉시 포기"가 걸리지 않는다). 레벨마다 새 예산을
        # 주는 대신, 이 호출 전체에 하나의 데드라인을 걸어 아래
        # _find_sibling_by_controltype에 전달한다 — 몇 번째 레벨이든
        # 재시도가 몇 번 남았든, 데드라인을 넘기면 즉시 전체 탐색을
        # 중단한다.
        deadline = time.time() + self._SIBLING_SETTLE_DEADLINE
        cur = elem
        remaining = max_up
        level = 0
        while remaining > 0:
            if time.time() >= deadline:
                log(f"[inspect] _ancestor_sibling_selector: deadline "
                    f"({self._SIBLING_SETTLE_DEADLINE}s) exceeded after "
                    f"{level} ancestor level(s) — giving up instead of "
                    "climbing further")
                return None
            ancestor = self._nearest_named_ancestor(cur, max_up=remaining)
            if ancestor is None:
                if level == 0:
                    log("[inspect] _ancestor_sibling_selector: no named ancestor found")
                return None
            level += 1
            remaining -= 1  # conservative: one named ancestor consumed, regardless of hop count
            # 2026-08-08: identify WHICH ancestor this was — the earlier logs
            # only said "named ancestor #N", leaving no way to tell from the
            # recording log alone whether H1 (the clicked control is
            # genuinely nested a level deeper than its siblings) or something
            # else is going on. automationId/name/controlType here are cheap
            # (already-resolved COM properties, no extra tree walk).
            try:
                anc_id = ancestor.CurrentAutomationId or ""
                anc_class = ancestor.CurrentClassName or ""
                anc_name = ancestor.CurrentName or ""
                anc_ct = ancestor.CurrentControlType
            except Exception:
                anc_id, anc_class, anc_name, anc_ct = "?", "?", "?", "?"
            # 2026-08-08 (H4, see docstring): never search children on the
            # climbed reference directly — refetch a clean one first.
            refetched = self._refetch_ancestor_clean(elem, anc_id, anc_class, anc_name)
            search_from = refetched if refetched is not None else ancestor
            hit = self._find_sibling_by_controltype(search_from, ct, target_rect,
                                                      target_hwnd, target_name,
                                                      deadline)
            if hit is not None:
                idx, count = hit
                return ancestor, idx, count
            log(f"[inspect] _ancestor_sibling_selector: named ancestor #{level} "
                f"(id={anc_id!r} name={anc_name!r} controlType={anc_ct!r}) has "
                f"no matching controlType={ct!r} child — trying the next named "
                f"ancestor above it ({remaining} hop(s) left)")
            cur = ancestor
        return None

    def _refetch_ancestor_clean(self, elem, anc_id, anc_class, anc_name):
        """Re-resolves an ancestor by its own AutomationId/ClassName/Name via
        a plain property FindFirst, discarding whatever COM reference
        _nearest_named_ancestor's climb produced. See
        _ancestor_sibling_selector's "Round 3" docstring — measured
        (poc/probe_ancestor_chain.py --walker-vs-refetch, one COM session):
        a reference obtained by climbing UP FROM element X omits X from its
        own children enumeration afterward; a reference to the identical
        element obtained by property search does not. Searches from
        _search_root_for(elem) — the same window-root resolution every other
        FindAll in this class already uses (§ its own docstring). Returns
        None (falls back to the climbed reference) if nothing is given to
        search on, the root can't be resolved, or FindFirst raises/misses —
        never worse than not having tried."""
        if not anc_id and not anc_class and not anc_name:
            log("[inspect] _refetch_ancestor_clean: no automationId/className/name "
                "to search on — falling back to the climbed reference")
            return None
        root = self._search_root_for(elem)
        if root is None:
            log("[inspect] _refetch_ancestor_clean: _search_root_for(elem) found no "
                "window root — falling back to the climbed reference")
            return None
        conds = []
        if anc_id and anc_id != "?":
            conds.append(self._uia.CreatePropertyCondition(30011, anc_id))     # AutomationId
        if anc_class and anc_class != "?":
            conds.append(self._uia.CreatePropertyCondition(30012, anc_class))  # ClassName
        if anc_name and anc_name != "?":
            conds.append(self._uia.CreatePropertyCondition(30005, anc_name))   # Name
        if not conds:
            return None
        cond = conds[0]
        for c in conds[1:]:
            cond = self._uia.CreateAndCondition(cond, c)
        try:
            found = root.FindFirst(7, cond)  # TreeScope_Subtree
        except Exception as e:
            log(f"[inspect] _refetch_ancestor_clean: FindFirst raised: {e}")
            return None
        if not found:
            log(f"[inspect] _refetch_ancestor_clean: FindFirst found nothing under "
                f"the resolved root for id={anc_id!r} class={anc_class!r} "
                f"name={anc_name!r} — falling back to the climbed reference")
            return None
        return found

    # 2026-08-09/10 (형제 개수 널뛰기 조사): poc/probe_ancestor_chain.py
    # --sample으로 실측 — 폴더 진입/트리 펼치기 직후 조상의 자식 개수가
    # 실제 값으로 안정되기까지 트리는 0.6~0.9s, **리스트(폴더 진입)는 최대
    # 1.8s**까지 걸리는 걸 직접 재현 확인(project→code-generator 진입,
    # 33→26으로 정확히 1800ms/샘플#6에서 수렴, 그 전까진 6번 연속 이전
    # 값). 스크롤 위치를 바꿔도 개수가 그대로였으므로(가상화 가설은 리스트
    # 컨트롤에서는 기각) 원인은 순수 타이밍 — 조회 자체를 몇 번 짧게
    # 재시도하면 해결된다. 기존 예산(3회×0.3s=0.9s)은 실측된 1.8s의 절반
    # 밖에 안 돼 부족했다 — 실측치 위에 여유를 둔 10회×0.3s(=2.7s)로 확장.
    _SIBLING_SETTLE_RETRIES = 10
    _SIBLING_SETTLE_INTERVAL = 0.3
    # 2026-08-10 (사용자 설계 제안): whole-call deadline for
    # _ancestor_sibling_selector, shared across every ancestor level it
    # climbs — replaces the old per-level budget that multiplied by climb
    # depth (up to 4 x 2.7s = 10.8s, measured on FileZilla's TreeView where
    # indentation makes every rect miss the exact-match early-exit in
    # _find_sibling_by_controltype). 1.0s total, no matter how many levels.
    _SIBLING_SETTLE_DEADLINE = 1.0

    def _find_sibling_by_controltype(self, ancestor, ct, target_rect, target_hwnd,
                                      target_name, deadline=None):
        """DIRECT children of ancestor (TreeScope_Children — see
        _ancestor_sibling_selector's docstring for why not Subtree) matching
        ct, narrowed to the one matching target_rect (+ hwnd/name tiebreaker).
        Returns (index, count) or None. Split out of
        _ancestor_sibling_selector so its climb-to-the-next-ancestor loop can
        retry this cheaply at each level (2026-08-08).

        Retries the whole census a few times on a miss (2026-08-09) — a miss
        right after a state-changing action (folder navigation, tree expand)
        is very often the ancestor still settling, not a real structural
        absence (see poc/probe_ancestor_chain.py --sample measurements).

        `deadline` (2026-08-10, whole-call budget) is a `time.time()`-based
        cutoff shared across every ancestor level _ancestor_sibling_selector
        climbs, checked before each retry sleep — see its definition for why
        a per-level budget alone (this function retrying up to
        _SIBLING_SETTLE_RETRIES times at EVERY level) wasn't enough."""
        # 2026-08-10 (사용자 실측 — 캡처 지연 3~4초): 조상에 그 controlType의
        # 자식이 "하나도 없음"은 구조적으로 불가능한 상태(Pane/panel 같은
        # 컨테이너는 애초에 ListItem/TreeItem을 못 가짐)라 아무리 기다려도
        # 안 바뀐다 — 재시도할 가치가 없다. "형제는 있는데 rect가 하나도 안
        # 맞음"만 타이밍 문제일 수 있어 재시도 가치가 있다(원래 7번 이슈가
        # 고치려던 것). 이 둘을 구분하지 않고 매 조상 레벨마다 3회씩 재시도한
        # 게 _ancestor_sibling_selector의 4단계 climb과 곱해져 최대 2.4초가
        # 그냥 버려지고 있었다 — "자식 0개"는 첫 시도에서 바로 포기한다.
        last_log = None
        for attempt in range(self._SIBLING_SETTLE_RETRIES):
            if attempt > 0:
                if deadline is not None and time.time() >= deadline:
                    log(f"[inspect] _ancestor_sibling_selector: shared "
                        f"deadline exceeded before retry attempt "
                        f"{attempt + 1}/{self._SIBLING_SETTLE_RETRIES} — "
                        "stopping this level's retries early")
                    break
                time.sleep(self._SIBLING_SETTLE_INTERVAL)
            try:
                items = ancestor.FindAll(
                    2, self._uia.CreatePropertyCondition(30003, ct))  # TreeScope_Children
            except Exception as e:
                last_log = (f"[inspect] _ancestor_sibling_selector: FindAll under "
                            f"ancestor raised: {e}")
                continue
            if not items or not items.Length:
                last_log = (f"[inspect] _ancestor_sibling_selector: ancestor has no "
                            f"direct children of controlType={ct!r} — structurally "
                            f"impossible to settle into, not retrying")
                break
            # 2026-08-10 (settle-vs-structural diagnosis): capture every
            # candidate's live rect on a miss so a reproduction recording can
            # show whether they drift toward target_rect across attempts
            # (still settling — worth the retry budget) or sit unrelated to
            # it the whole time (never going to match — retrying is wasted
            # time that only grows the worker queue's backlog).
            candidate_rects = []
            for i in range(items.Length):
                try:
                    it = items.GetElement(i)
                    ir = it.CurrentBoundingRectangle
                    it_rect = (ir.left, ir.top, ir.right, ir.bottom)
                    candidate_rects.append(it_rect)
                    if it_rect != target_rect:
                        continue
                    if target_hwnd:
                        if (it.CurrentNativeWindowHandle or 0) != target_hwnd:
                            continue
                    else:
                        if (it.CurrentName or "") != target_name:
                            continue
                except Exception:
                    continue
                if attempt > 0:
                    log(f"[inspect] _ancestor_sibling_selector: matched on retry "
                        f"attempt {attempt + 1}/{self._SIBLING_SETTLE_RETRIES}")
                return i, items.Length
            # a candidate whose rect matches target_rect exactly but was
            # skipped above (hwnd/name tiebreaker failed) means the position
            # itself is settled — the element sitting there is provably a
            # different one, not the one we're waiting for.
            rect_matched_wrong_identity = target_rect in candidate_rects
            log(f"[diag-settle] attempt {attempt + 1}/{self._SIBLING_SETTLE_RETRIES} "
                f"target_rect={target_rect!r} candidates={candidate_rects!r}")
            last_log = (f"[inspect] _ancestor_sibling_selector: {items.Length} "
                        f"same-controlType sibling(s) found but none matched "
                        f"target_rect={target_rect!r} (hwnd={target_hwnd!r} "
                        f"name={target_name!r}) (attempt {attempt + 1}/"
                        f"{self._SIBLING_SETTLE_RETRIES})")
            # 2026-08-10 (user-measured 4.5s capture delay, FileZilla local
            # list): a rect that matches target_rect EXACTLY but still fails
            # the hwnd/name tiebreaker is not "still settling" — a settling
            # list's rects are still shifting into place, so an exact rect
            # match this early means the position is already final and the
            # element sitting there is provably a different one (the row was
            # recycled/renamed under it, e.g. by the time a backlogged worker
            # got here the user had already navigated further). No amount of
            # waiting fixes an identity that has already changed — retrying
            # here only burns the budget and grows the queue backlog that
            # caused the staleness in the first place. Give up immediately
            # instead of spending the remaining attempts.
            if rect_matched_wrong_identity:
                log(f"[inspect] _ancestor_sibling_selector: target_rect="
                    f"{target_rect!r} matched exactly but failed the hwnd/name "
                    f"tiebreaker — not a settling timing issue (rects are "
                    f"already final), the element there is a different one; "
                    f"giving up after attempt {attempt + 1}/"
                    f"{self._SIBLING_SETTLE_RETRIES} instead of burning the "
                    f"remaining retry budget")
                break
        if last_log:
            log(last_log)
        return None

    def element_at(self, x, y):
        # DIAGNOSTIC: reset the decision-path trace for this call — see
        # __init__'s _last_trace comment. Every branch below records which
        # path it took; _inspect() reads this back into one [trace] log line.
        trace = {"picked_by": None, "root_hwnd": None, "root_ok": None}
        self._last_trace = trace
        pt = wintypes.POINT(int(x), int(y))
        elem = self._uia.ElementFromPoint(pt)
        try:
            raw = self.describe(elem) if elem is not None else {}
            trace["raw"] = (f"id={raw.get('automationId')!r} name={raw.get('name')!r} "
                             f"rect={raw.get('rect')!r} hwnd={raw.get('hwnd')!r}")
            # 2026-08-05: 포맷된 문자열만 남기면 이 최초 관측값을 나중에 쓸 수
            # 없다. _inspect()의 죽은-요소 복구가 이걸 필요로 한다 — 아래
            # 주석 참고(요소가 이 describe와 _inspect의 두 번째 describe
            # 사이에서 죽는 경우, 살아있던 유일한 스냅샷이 바로 이것이다).
            trace["raw_info"] = raw
        except Exception:
            trace["raw"] = "ERR"
            trace["raw_info"] = {}
        elem_was_deepened = False
        if elem is not None:
            try:
                needs_deepen = not elem.CurrentAutomationId
                if not needs_deepen:
                    # QML은 컨테이너에도 AutomationId를 채우므로 ID 존재만으로는
                    # leaf 요소라고 보장 못 함 — bounding rect가 크거나
                    # ControlType이 컨테이너 계열이면 ID가 있어도 계속 파고든다.
                    try:
                        rect = elem.CurrentBoundingRectangle
                        w = rect.right - rect.left
                        h = rect.bottom - rect.top
                        ct = elem.CurrentControlType
                        container_types = {50021, 50033, 50026, 50008}  # ToolBar, Pane, Group, List
                        if ct in container_types or w > 80 or h > 80:
                            needs_deepen = True
                    except Exception:
                        pass
                if needs_deepen:
                    deeper = None
                    root_hwnd = self.resolve_root_hwnd(elem)
                    if not root_hwnd:
                        # 2026-08-05 (TeamViewer "빠른 연결 허용"/"Easy Access"
                        # 트리거 실측, 2차 수정 — 1차는 self.target_hwnds를
                        # 참조하는 버그였다: 그 속성은 Recorder 클래스에 있고
                        # UIAInspector에는 없어 AttributeError가 조용히 삼켜져
                        # root_hwnd/root_ok가 트레이스에 아예 안 찍혔다):
                        # 깊은 Chromium/React DOM은 resolve_root_hwnd()의
                        # 15-hop 상한을 통째로 넘을 수 있다 — root_hwnd=0이 되어
                        # smallest_element_at()가 아예 시도되지도 못하고
                        # depth-capped _deepen()로 떨어졌고, _deepen()도 못
                        # 찾아 결국 nameless raw 요소가 그대로 남아
                        # _nearest_named_ancestor()가 창의 94%를 덮는 훨씬
                        # 못 쓸 조상으로 대체해버렸다. resolve_root_hwnd() 자신을
                        # 고치면 안 된다 — 그 0 반환값은 _inspect()의 자기-오염
                        # 방지 가드(2026-08-04 주석 참고)에서 그대로 신뢰된다.
                        # 같은 클래스의 _search_roots_for()가 이미 똑같은
                        # 상황에서 쓰는 검증된 폴백(foreground window, GA_ROOT로
                        # 정규화)을 그대로 재사용한다 — smallest_element_at()가
                        # 찾은 결과는 어차피 _inspect()의 별도 tracked-window
                        # 검사를 다시 통과해야 하므로, 여기서 잘못된 창을
                        # 골라도 그 검사가 걸러낸다.
                        fg = ctypes.windll.user32.GetForegroundWindow()
                        if fg:
                            root_hwnd = ctypes.windll.user32.GetAncestor(fg, GA_ROOT) or fg
                    root = self.from_handle_safe(root_hwnd)
                    trace["root_hwnd"] = root_hwnd
                    trace["root_ok"] = bool(root)
                    if root:
                        deeper = self.smallest_element_at(root, int(x), int(y))
                        if deeper is not None:
                            trace["picked_by"] = "smallest_element_at"
                    if deeper is None:
                        deeper = self._deepen(elem, int(x), int(y))
                        if deeper is not None:
                            trace["picked_by"] = "_deepen"
                    # Adopt even when the result has neither id nor name — the
                    # anchor_path machinery downstream turns that into a
                    # relative XPath, and if even that fails the event becomes
                    # an explicit FAIL step. The old rule kept the ORIGINAL
                    # element in that case, which is how a 94%-of-window
                    # container ended up recorded as the click target
                    # (TeamViewer, 2026-08-03).
                    if deeper is not None:
                        elem = deeper
                        elem_was_deepened = True
                    else:
                        trace["picked_by"] = "raw-kept (deepen found nothing)"
                else:
                    trace["picked_by"] = "raw-direct"
            except Exception:
                pass
        try:
            aid = elem.CurrentAutomationId if elem is not None else ""
            name = elem.CurrentName if elem is not None else ""
            # 7-Zip's SysListView32 rows expose an inner "Edit"-typed surrogate
            # cell that already carries the correct row Name (unlike VSCode's
            # blank/misleading "이름" surrogate, 2026-07-08) — so the
            # (not aid and not name) guard above never fires, and the capture
            # keeps the surrogate. Confirmed 2026-07-15 (probe_wad.cjs):
            # WinAppDriver's REST element/click on that surrogate is a
            # silent no-op (list unchanged before/after), while a direct COM
            # InvokePattern.Invoke() on the parent ListItem genuinely
            # navigates. Climb whenever the leaf is an unlabeled-id Edit,
            # regardless of whether its own Name looks fine — a real
            # standalone Edit field (not inside a list row) has no
            # ListItem/TreeItem ancestor, so _nearest_row_ancestor returns
            # None there and this is a no-op for it.
            is_unlabeled_edit = False
            if elem is not None and not aid:
                try:
                    is_unlabeled_edit = elem.CurrentControlType == 50004  # Edit
                except Exception:
                    pass
            if elem is not None and (aid in self.GENERIC_CELL_AUTOMATION_IDS
                                      or (not aid and not name) or is_unlabeled_edit):
                row = self._nearest_row_ancestor(elem)
                if row is not None:
                    trace["picked_by"] = "row-ancestor"
                    return row
                # 2026-08-05 (TeamViewer "빠른 연결 허용"/"Easy Access" 트리거
                # 실측): elem_was_deepened가 참이면 elem은 이미
                # smallest_element_at()/_deepen()이 명시적으로 골라낸, 창을
                # 덮지 않는 작고 위치가 정확한 요소다. 그런데 바로 아래
                # _nearest_named_ancestor()가 "이름이 없다"는 이유만으로 그
                # 결과를 다시 위로 4단계까지 올려버려, 정확히 그 큰 컨테이너
                # 문제를 피하려고 만든 결과를 도로 큰 컨테이너로 바꿔치기했다
                # (실측: 이름 없는 위치-정확 요소 대신 창의 94%를 덮는 root
                # 컨테이너가 채택됨). 이 조상-탐색은 딥닝이 아예 실패해서
                # elem이 여전히 최초 ElementFromPoint의 raw 히트인 경우에만
                # 의미가 있다(원래 의도: HeidiSQL SplitButton의 장식용 화살표
                # 글리프처럼, 진짜 컨트롤은 한 단계 위에 있는 raw-hit 케이스,
                # 2026-07-29). 딥닝이 성공한 결과에는 적용하지 않는다 —
                # 이름이 없어도 anchor_path 메커니즘이 그 작고 정확한 위치를
                # 그대로 살릴 수 있다.
                if not aid and not name and not elem_was_deepened:
                    named = self._nearest_named_ancestor(elem)
                    if named is not None:
                        trace["picked_by"] = "named-ancestor"
                        return named
        except Exception:
            pass
        return elem

    def _deepen(self, elem, x, y, depth=0, skip_overlay=False):
        """Walk ControlView tree to find the deepest child containing (x, y).
        Depth cap was 5, tuned for WPF/UWP trees. Chromium/Electron a11y trees
        nest list rows much deeper (row wrapper > flex container > icon/text
        spans, ...), so a click on dynamic content (chat history rows, header
        icons) hit the cap before reaching the actual leaf and fell back to
        reporting the whole scroll container ("사이드바") as the clicked
        element — static top-level buttons (shallower trees) were unaffected,
        which is why only some clicks showed the wrong element.

        skip_overlay: ignore XAML "Light Dismiss" scrims while descending —
        used by element_under_overlay() to hit-test what the user actually
        clicked when the async inspection raced a menu/flyout opening and
        the full-window overlay already covers the point."""
        if depth >= 15:
            return None
        try:
            walker = self._uia.ControlViewWalker
            child = walker.GetFirstChildElement(elem)
            while child is not None:
                skip = False
                if skip_overlay:
                    try:
                        if child.CurrentAutomationId == "Light Dismiss":
                            skip = True
                    except Exception:
                        pass
                if not skip:
                    try:
                        rect = child.CurrentBoundingRectangle
                        if rect.left <= x <= rect.right and rect.top <= y <= rect.bottom:
                            deeper = self._deepen(child, x, y, depth + 1, skip_overlay)
                            return deeper if deeper is not None else child
                    except Exception:
                        pass
                try:
                    child = walker.GetNextSiblingElement(child)
                except Exception:
                    break
        except Exception:
            pass
        return None

    def element_under_overlay(self, x, y):
        """Re-resolve the element beneath a XAML light-dismiss overlay.

        ElementFromPoint returns the topmost element — once a menu/flyout is
        open, that is the full-window "Light Dismiss" scrim, not the control
        the user clicked half a second earlier (worker-thread inspection lag).
        The real control is still present in the same top-level window's
        ControlView tree as a SIBLING subtree of the overlay, so descend from
        the foreground top-level window while skipping the overlay. Returns
        None when nothing better than the window itself is found.

        Try smallest_element_at() (FindAll + smallest-area-wins, no depth
        cap) before falling back to _deepen() (first-containing-child walk,
        capped at 15 ancestor hops). This function used to call ONLY
        _deepen() — fine for the XAML light-dismiss case it was built for
        (2026-07-12, shallow native trees), but measured 2026-08-04
        (TeamViewer): element_at()'s OWN first pass already found the
        correct WebView2 button via smallest_element_at() (its rect and
        name showed up correctly in the pre-rejection [inspect] log), then
        got rejected as untracked (hwnd==0, routine for web content) and
        handed to THIS function to recover — which used only the
        depth-capped _deepen() and came back empty, because a Chromium/React
        DOM subtree is routinely deeper than 15 hops. The recovery was
        strictly weaker than the pass that had already succeeded moments
        earlier, so TeamViewer's WebView2 clicks lost their selectors
        entirely. smallest_element_at() also filters out anything covering
        >=80% of the window (WINDOW_FILL_RATIO), which incidentally screens
        out a full-window light-dismiss scrim on its own — the explicit
        AutomationId=="Light Dismiss" check below stays as a second layer,
        not the only one."""
        try:
            hwnd = foreground_top_window()
            if not hwnd:
                return None
            root = self._uia.ElementFromHandle(hwnd)
            if root is None:
                return None
            deeper = self.smallest_element_at(root, int(x), int(y))
            if deeper is None:
                deeper = self._deepen(root, int(x), int(y), skip_overlay=True)
            if deeper is None:
                return None
            try:
                if deeper.CurrentAutomationId == "Light Dismiss":
                    return None
            except Exception:
                pass
            return deeper
        except Exception:
            return None

    # ── anchor 기반 relative XPath (2026-07-10 지시) ─────────────────────────
    # 좌표 재생이 전면 금지되면서 셀렉터 없는 이벤트는 재생 불가(FAIL)가 된다.
    # 유니크 AutomationId/Name이 없는 요소는 "안정적 ID를 가진 조상 anchor"까지
    # 걸어 올라가 anchor 기준 relative XPath(/Tag[i]/... 형태)를 캡처한다.
    ANCHOR_MAX_UP = 8          # anchor 탐색 최대 상승 깊이
    ANCHOR_MAX_SIBLINGS = 60   # 레벨당 형제 스캔 상한 — 초과 시 (가상화 리스트
                               # 등) 인덱스가 불안정하므로 anchor 포기

    def anchor_path(self, elem):
        """Return (anchor_automation_id, rel_path) — e.g. ("NumberPad",
        "/Button[3]") — for an element lacking its own id/name, or None.
        rel_path steps are ControlType tags with 1-based same-type sibling
        indices, matching WinAppDriver's XML view (tag name == ControlType)."""
        try:
            walker = self._uia.ControlViewWalker
            steps = []
            cur = elem
            for _ in range(self.ANCHOR_MAX_UP):
                try:
                    ct = cur.CurrentControlType
                except Exception:
                    return None
                tag = UIA_CONTROL_TYPES.get(ct)
                if not tag:
                    return None
                idx = 1
                scanned = 0
                sib = walker.GetPreviousSiblingElement(cur)
                while sib is not None:
                    scanned += 1
                    if scanned > self.ANCHOR_MAX_SIBLINGS:
                        return None
                    try:
                        if sib.CurrentControlType == ct:
                            idx += 1
                    except Exception:
                        pass
                    sib = walker.GetPreviousSiblingElement(sib)
                steps.append(f"/{tag}[{idx}]")
                parent = walker.GetParentElement(cur)
                if parent is None:
                    return None
                aid = ""
                try:
                    aid = parent.CurrentAutomationId or ""
                except Exception:
                    aid = ""
                # 안정적 anchor 조건: 비어있지 않고, QML dotted path가 아닌
                # AutomationId. 순수 숫자 AutomationId는 부모가 가상화 리스트/
                # 트리 아이템(런타임 슬롯 인덱스, 스크롤 시 값이 바뀜)일 때만
                # 거부 — Win32 다이얼로그 컨트롤의 숫자 리소스 ID는 재시작해도
                # 고정이라 anchor로 신뢰 가능 (2026-07-13, server.js
                # SLOT_INDEX_CONTROL_TYPES와 동일 기준).
                parent_ct = None
                try:
                    parent_ct = UIA_CONTROL_TYPES.get(parent.CurrentControlType)
                except Exception:
                    pass
                is_slot_index = aid.isdigit() and parent_ct in ("ListItem", "TreeItem", "DataItem")
                # Delphi/VCL 컨트롤은 실제 AutomationId 없이 자기 hwnd가 그
                # 자리에 채워진다(2026-07-29, HeidiSQL 실측: id 19개 중 13개가
                # NativeWindowHandle과 정확히 일치) — 실행마다 바뀌므로 anchor로
                # 못 씀. server.js의 isWindowHandleId()와 동일 판정 기준.
                is_hwnd_id = False
                if aid.isdigit():
                    try:
                        is_hwnd_id = int(aid) == (parent.CurrentNativeWindowHandle or 0) \
                            and parent.CurrentNativeWindowHandle
                    except Exception:
                        is_hwnd_id = False
                if aid and "." not in aid and not is_slot_index and not is_hwnd_id:
                    return aid, "".join(reversed(steps))
                cur = parent
        except Exception:
            pass
        return None

    # UIA_ExpandCollapsePatternId — ComboBox 드롭다운/메뉴바 MenuItem/트리 +-
    # 토글 판별용. 2026-07-13 진단(poc/diag_expandcollapse.py)으로 실측:
    # 셋 다 이 패턴을 지원하며, 일반 클릭(InvokePattern)만으로는 ComboBox
    # 드롭다운이 안 열리거나(PuTTY) 하위 항목이 별도 최상위 팝업 창에 생겨
    # 원래 요소 서브트리에서 안 보임(FileZilla 메뉴바, #32768 클래스).
    EXPAND_COLLAPSE_PATTERN_ID = 10005

    # UIA_LegacyIAccessiblePatternId — MSAA/IAccessible bridge. Tried only as
    # a last-resort naming fallback in describe() when both CurrentName and
    # CurrentAutomationId come back empty: some native (Win32/MFC/VCL/wx)
    # controls populate the older MSAA Name/Description/Help properties
    # without ever populating the modern UIA Name/AutomationId properties.
    LEGACY_IACCESSIBLE_PATTERN_ID = 10018

    def has_expand_collapse(self, elem):
        try:
            return elem.GetCurrentPattern(self.EXPAND_COLLAPSE_PATTERN_ID) is not None
        except Exception:
            return False

    # ControlType=Tree / TreeItem UIA constants.
    CT_TREE = 50023
    CT_TREE_ITEM = 50024

    def tree_item_at_row(self, tree_elem, y):
        """When a click lands in a TreeItem row's indent/glyph area (outside
        every item's own label rect, so element_at() falls all the way back
        to the whole Tree control), prefer the specific row whose vertical
        band contains the click's y over the bare Tree — replaying a click
        on the Tree's center is a wrong node depending on what's currently
        painted there (confirmed 2026-07-13: PuTTY's Window +/- toggle fell
        back to the whole 'Category:' Tree). Scans all TreeItem descendants
        (not just direct children) since nested category rows are present in
        the UIA tree regardless of visual expand state (2026-07-11 anchor
        capture already relies on this)."""
        try:
            items = tree_elem.FindAll(4, self._uia.CreateTrueCondition())  # TreeScope_Descendants
        except Exception:
            return None
        for i in range(items.Length):
            it = items.GetElement(i)
            try:
                if it.CurrentControlType != self.CT_TREE_ITEM:
                    continue
                r = it.CurrentBoundingRectangle
                if r.top <= y <= r.bottom:
                    return it
            except Exception:
                continue
        return None

    # ── open-dropdown item resolution (2026-07-31) ──────────────────────────
    # Clicking an item in an OPEN combo dropdown hit-tests to the COMBO, never
    # to the item: ElementFromPoint returns the combo whose bounding rect is
    # still the COLLAPSED box, so the "click point outside the adopted rect"
    # guard below used to throw the selector away and the event degraded to a
    # click on whatever panel sat underneath (measured 2026-07-31 on HeidiSQL's
    # network-type combo: every item click was recorded as `click '설정'
    # TTabSheet`).
    #
    # Win32 ComboBoxEx is two stacked controls sharing one rect:
    #   TComboBoxEx (Pane)  Name = the currently selected value, id = its hwnd
    #                       -> patterns: Legacy only, CANNOT be expanded
    #   ComboBox            Name = '', id = ''
    #                       -> patterns: ExpandCollapse, Value, Legacy
    # Only the inner one is drivable, and a selector built from the outer one's
    # Name can never match before the value is already selected.
    #
    # The open list DOES publish its items (18 measured), each supporting
    # Invoke/SelectionItem -- but for ComboBoxEx every item Name is EMPTY
    # (owner-drawn, icon-per-item), so an item can only be addressed by its
    # POSITION IN THE LIST. That is a structural index, not a screen
    # coordinate, and it matches the slot-index handling the generator already
    # uses for nameless ListItem/TreeItem/DataItem rows.
    CT_COMBO_BOX = 50003
    CT_LIST_ITEM = 50007
    CT_MENU = 50009
    CT_MENU_ITEM = 50011

    def _is_combo_like(self, info):
        return (info.get("controlType") == "ComboBox"
                or "ComboBox" in (info.get("className") or ""))

    def _is_menu_like(self, info):
        # Measured 2026-08-04 (HeidiSQL "더 보기"): the TRIGGER is a VCL
        # SplitButton, not a MenuItem — the assumption that it would mirror
        # ComboBox's own controlType was wrong, confirmed via the diagnostic
        # log in snapshot_open_menu(). SplitButton IS the trigger type
        # (button + attached menu is exactly what that UIA control type
        # means), so include it alongside MenuItem for triggers that
        # genuinely are plain popup-menu items (e.g. a submenu opener inside
        # an already-open menu).
        return info.get("controlType") in ("MenuItem", "SplitButton")

    def settled_subtree_count(self, root, timeout=8.0, quiet_for=1.5):
        """Poll the subtree size until it stops growing, then return it.

        An embedded-Chromium app publishes its accessibility tree
        progressively. Measured 2026-08-03 (TeamViewer 15.79), the SAME window
        sampled repeatedly from a cold start:

            t=0s   t=1s   t=3s   t=7s
              25     26     61     61

        Sampling once reports the observer's timing, not the app. That single
        mistake is what produced the 2026-07-31 "TeamViewer is Tier 4, not
        automatable" verdict, which did not survive re-measurement.
        """
        best, stable_since = 0, None
        deadline = time.time() + timeout
        while True:
            try:
                found = root.FindAll(7, self._uia.CreateTrueCondition())
                cur = found.Length if found else 0
            except Exception:
                cur = 0
            if cur > best:
                best, stable_since = cur, time.time()
            elif stable_since is None:
                stable_since = time.time()
            if time.time() - stable_since >= quiet_for or time.time() >= deadline:
                return best
            time.sleep(0.3)

    def _search_roots_for(self, elem):
        """Window elements to run FindAll from, most specific first.

        resolve_root_hwnd() walks up looking for a NativeWindowHandle and
        returns 0 when it finds none — measured 2026-07-31: an item inside an
        open combo dropdown is exactly that case, and ElementFromHandle(0)
        then raises. Fall back to the foreground window, which during capture
        is the app window that owns both the combo and its popup list.

        Every candidate is normalised through GA_ROOT — the same walk
        describe() already does for windowTitle. Without it this returns the
        CONTROL, not a window, whenever the control is itself a window:
        measured 2026-08-03 on HeidiSQL's network-type combo, a VCL
        TComboBoxEx owns its own hwnd (which is also why its AutomationId is
        an hwnd), so resolve_root_hwnd() returned 3674908 — the combo itself —
        and open_dropdown_item_at()'s FindAll ran inside the COLLAPSED combo's
        subtree, which structurally cannot contain the open list (the capture
        log shows the watcher registering the list's own top-level windows,
        hwnd=660188/1642736, the instant it opened). Every item click then
        fell through to the light-dismiss fallback and was recorded as a click
        on whatever sat BEHIND the open list — a '자격 증명 프롬프트' CheckBox
        the user never touched. Silent false capture, worse than a FAIL step.
        """
        seen = set()
        for hwnd in (self.resolve_root_hwnd(elem),
                     ctypes.windll.user32.GetForegroundWindow()):
            if not hwnd:
                continue
            root_hwnd = ctypes.windll.user32.GetAncestor(hwnd, GA_ROOT) or hwnd
            if root_hwnd in seen:
                continue
            seen.add(root_hwnd)
            try:
                root = self._uia.ElementFromHandle(root_hwnd)
                if root:
                    yield root
            except Exception:
                continue

    def _search_root_for(self, elem):
        """The single most specific search root — see _search_roots_for()."""
        for root in self._search_roots_for(elem):
            return root
        return None

    def _same_process_top_windows(self, elem, extra_pids=None):
        """Every visible top-level window owned by elem's own process (plus
        any caller-supplied extra_pids — see below).

        A Win32 popup menu (TrackPopupMenu — "더 보기") always renders as a
        SEPARATE top-level window, and measured 2026-08-04 (HeidiSQL): unlike
        a modal dialog, it does not become GetForegroundWindow()'s return
        value. _search_roots_for()'s two candidates (the trigger's own
        window, the foreground window) therefore both search the wrong
        subtree and silently find nothing — snapshot_open_menu() never
        cached the popup's items, so the picking click had nothing to
        resolve against. A fresh same-PID window enumeration finds it
        without depending on watcher-thread timing or foreground/activation
        behavior, which differs per control (a combo's own owner-drawn list
        is a CHILD of the main window and doesn't need this at all).

        2026-08-08 (FileZilla 빠른 연결 지우기 실측, poc/probe_filezilla_
        quickconnect_dropdown.py로 확인): 이 함수가 PID를 구하는 두 경로 —
        elem.CurrentNativeWindowHandle(항목 자체가 lightweight라 보통 0)과
        foreground_top_window()(팝업이 열려 있는 동안 신뢰 불가, 바로 위
        docstring이 이미 그렇게 말한다) — 가 둘 다 실패하면 pid=0으로
        조용히 빈 리스트를 반환한다. 실측 로그(`searched:
        ['hwnd=1575272: not a Menu root (fast path)']`)가 정확히 이 경우 —
        진짜 열려 있던 팝업(#32768 Menu, 새 최상위 창)이 후보에 아예 없었다.
        Recorder는 세션 내내 신뢰성 있는 PID 집합을 이미 별도로 추적한다
        (self._target_pids() — watcher/self-heal이 채움). 호출자가 그 집합을
        extra_pids로 넘기면, elem 기반 유도가 실패해도 이 신뢰할 수 있는
        PID들로 계속 검색한다 — 매 호출마다 클릭된 요소 하나에서 PID를
        재유도하는 취약한 단일 경로에 기대지 않는다."""
        pids = set(extra_pids or ())
        try:
            h = elem.CurrentNativeWindowHandle
            if h:
                p = pid_of_hwnd(h)
                if p:
                    pids.add(p)
        except Exception:
            pass
        if not pids:
            p = pid_of_hwnd(foreground_top_window())
            if p:
                pids.add(p)
        if not pids:
            return []
        out = []
        for hwnd in visible_toplevel_windows():
            try:
                if pid_of_hwnd(hwnd) in pids:
                    out.append(hwnd)
            except Exception:
                continue
        return out

    def _search_roots_for_menu(self, elem, extra_pids=None):
        """_search_roots_for() plus every same-process top-level window —
        see _same_process_top_windows()."""
        seen = set()
        for root in self._search_roots_for(elem):
            try:
                h = root.CurrentNativeWindowHandle
            except Exception:
                h = None
            if h:
                seen.add(h)
            yield root
        for hwnd in self._same_process_top_windows(elem, extra_pids=extra_pids):
            if not hwnd or hwnd in seen:
                continue
            seen.add(hwnd)
            try:
                root = self._uia.ElementFromHandle(hwnd)
                if root:
                    yield root
            except Exception:
                continue

    def from_handle_safe(self, hwnd):
        try:
            el = self._uia.ElementFromHandle(hwnd)
            return el if el else None      # comtypes returns NULL, not None
        except Exception:
            return None

    # A candidate covering at least this fraction of the window is a container,
    # not a target — same threshold as server.js WINDOW_FILL_RATIO.
    WINDOW_FILL_RATIO = 0.80

    def smallest_element_at(self, root, x, y):
        """The smallest element in root's subtree containing (x, y).

        Replaces the first-containing-child descent of _deepen(): that walk
        could not backtrack out of a dead-end branch, and its depth cap had to
        be retuned per UI framework. Selecting by area is independent of tree
        shape and depth.
        """
        try:
            arr = root.FindAll(7, self._uia.CreateTrueCondition())
        except Exception:
            return None
        if not arr or not arr.Length:
            return None
        try:
            wr = root.CurrentBoundingRectangle
            win_area = max(1, (wr.right - wr.left) * (wr.bottom - wr.top))
        except Exception:
            win_area = None
        els, rects = [], []
        for i in range(arr.Length):
            el = arr.GetElement(i)
            try:
                r = el.CurrentBoundingRectangle
            except Exception:
                continue
            rect = (r.left, r.top, r.right, r.bottom)
            if win_area:
                area = max(0, rect[2] - rect[0]) * max(0, rect[3] - rect[1])
                if area / win_area >= self.WINDOW_FILL_RATIO:
                    continue        # a window-filling container is not a target
            els.append(el)
            rects.append(rect)
        idx = smallest_rect_index(rects, x, y)
        return els[idx] if idx is not None else None

    def _inner_expandable_combo(self, elem):
        """The control that actually owns the dropdown. For a ComboBoxEx the
        outer wrapper cannot be expanded; its same-rect ComboBox sibling can."""
        try:
            if elem.GetCurrentPattern(self.EXPAND_COLLAPSE_PATTERN_ID):
                return elem
        except Exception:
            pass
        root = self._search_root_for(elem)
        if root is None:
            return None
        try:
            r = elem.CurrentBoundingRectangle
            cands = root.FindAll(7, self._uia.CreatePropertyCondition(
                30003, self.CT_COMBO_BOX))          # TreeScope_Subtree
        except Exception:
            return None
        for i in range(cands.Length):
            c = cands.GetElement(i)
            try:
                cr = c.CurrentBoundingRectangle
                if (abs(cr.left - r.left) <= 2 and abs(cr.top - r.top) <= 2
                        and abs(cr.right - r.right) <= 2
                        and c.GetCurrentPattern(self.EXPAND_COLLAPSE_PATTERN_ID)):
                    return c
            except Exception:
                continue
        return None

    def combo_item_self(self, elem, info):
        """The hit test landed ON a dropdown item (not on the combo).

        Measured 2026-07-31: whether ElementFromPoint returns the ListItem or
        the combo depends on timing — when the list has been open long enough
        for its items to reach the accessibility tree, the item itself comes
        back. That case never trips the "outside the adopted rect" guard, so
        open_dropdown_item_at() below never sees it; but for an owner-drawn
        ComboBoxEx the item has NO Name and NO AutomationId, so it still ends
        up with no usable selector and degrades into an anchor path or a FAIL
        step. Recognise it here and record the same
        combo + position pair.

        Returns (inner_combo, index, total) or None.
        """
        if info.get("controlType") != "ListItem":
            return None
        if info.get("name") or info.get("automationId"):
            return None                      # a named item needs none of this
        root = self._search_root_for(elem)
        if root is None:
            return None
        try:
            items = root.FindAll(7, self._uia.CreatePropertyCondition(
                30003, self.CT_LIST_ITEM))
            combos = root.FindAll(7, self._uia.CreatePropertyCondition(
                30003, self.CT_COMBO_BOX))
        except Exception:
            return None
        if not items or not items.Length:
            return None
        # Which combo owns this list? Exactly one should be expanded right now.
        expanded = []
        for i in range(combos.Length):
            c = combos.GetElement(i)
            try:
                ecp = c.GetCurrentPattern(self.EXPAND_COLLAPSE_PATTERN_ID)
                if ecp and ecp.QueryInterface(
                        self._mod.IUIAutomationExpandCollapsePattern
                ).CurrentExpandCollapseState == 1:      # Expanded
                    expanded.append(c)
            except Exception:
                continue
        if len(expanded) != 1:
            return None
        try:
            target_rect = elem.CurrentBoundingRectangle
        except Exception:
            return None
        for i in range(items.Length):
            try:
                r = items.GetElement(i).CurrentBoundingRectangle
            except Exception:
                continue
            if (r.left == target_rect.left and r.top == target_rect.top
                    and r.right == target_rect.right
                    and r.bottom == target_rect.bottom):
                return expanded[0], i, items.Length
        return None

    def menu_item_self(self, elem, info):
        """The hit test landed ON a popup menu item (not on its trigger).

        Mirrors combo_item_self() for HeidiSQL's "더 보기" overflow menu:
        the item is icon-only (no Name, no AutomationId — measured
        2026-08-04, PuTTY/TeamViewer/HeidiSQL logs all show the SAME shape:
        element_at() lands directly on a small, real, but nameless MenuItem
        rect, not on the trigger). A popup menu's items live under a
        separate `Menu` (ControlType 50009) container distinct from the
        `MenuItem` (50011) trigger that opened it — the same clean type
        split ComboBox/ListItem gives the combo case, so "which popup is
        open" is answered by "does a Menu container exist in the search
        root". The RETURNED element is still the trigger MenuItem, not the
        Menu container — osExpandCollapse.py needs something with
        ExpandCollapsePattern to re-invoke, and it needs to be the same kind
        of element open_menu_item_at()/snapshot_open_menu() already key the
        cache on, so replay and this direct-hit path describe identical
        things.

        Returns (trigger, index, total) or None.
        """
        if info.get("controlType") != "MenuItem":
            return None
        if info.get("name") or info.get("automationId"):
            return None                      # a named item needs none of this
        # The Menu container and the trigger can legitimately live in
        # DIFFERENT top-level windows (the popup vs. the main window that
        # owns the trigger button) — search every same-process window for
        # each independently rather than assuming one root has both.
        menu, items = None, None
        for root in self._search_roots_for_menu(elem):
            try:
                menus = root.FindAll(7, self._uia.CreatePropertyCondition(
                    30003, self.CT_MENU))
            except Exception:
                continue
            if not menus or menus.Length != 1:
                continue                     # 0 or >1 open popups — ambiguous
            try:
                cand_items = menus.GetElement(0).FindAll(
                    7, self._uia.CreatePropertyCondition(30003, self.CT_MENU_ITEM))
            except Exception:
                continue
            if cand_items and cand_items.Length:
                menu, items = menus.GetElement(0), cand_items
                break
        if menu is None or items is None:
            return None
        # Which element is the trigger? NOT found by "exactly one MenuItem
        # with ExpandCollapseState==Expanded" — measured 2026-08-04
        # (HeidiSQL "더 보기"): a VCL SplitButton's ExpandCollapseState is
        # always None/unreliable, the same disease snapshot_open_menu() hit,
        # and the real trigger isn't even a MenuItem to begin with (it's a
        # SplitButton — a different controlType this search wouldn't find
        # anyway). Reuse the reference snapshot_open_menu() already captured
        # at the one moment the trigger was reliably known: the click that
        # opened THIS SAME popup.
        c = self._menu_cache
        if not c or "trigger" not in c:
            return None
        trigger = c["trigger"]
        try:
            target_rect = elem.CurrentBoundingRectangle
        except Exception:
            return None
        for i in range(items.Length):
            try:
                r = items.GetElement(i).CurrentBoundingRectangle
            except Exception:
                continue
            if (r.left == target_rect.left and r.top == target_rect.top
                    and r.right == target_rect.right
                    and r.bottom == target_rect.bottom):
                return trigger, i, items.Length
        return None

    # How long a cached dropdown geometry stays usable. Only ever consumed by
    # the very next click, so this just bounds a stale cache.
    DROPDOWN_CACHE_TTL = 15.0

    def snapshot_open_dropdown(self, elem, info):
        """Record the geometry of a dropdown list WHILE IT IS STILL OPEN.

        Measured 2026-08-03 (HeidiSQL network-type combo): the click that
        selects an item is also the click that CLOSES the list, and the worker
        thread inspects 0.2-0.5s later — by then the list is gone. The capture
        log proves it in one run: a scroll over the open list found 18
        ListItems, while a click on an item found 0 from the same search root,
        and the combo's Name had already flipped to the newly selected value
        ('MySQL on RDS') by inspection time. With nothing to match, the hit
        test fell through to the light-dismiss fallback and recorded whatever
        the closed list had been covering — a '자격 증명 프롬프트' CheckBox the
        user never clicked. A silent false capture, worse than a FAIL step.

        So snapshot on the click that OPENS the list (that one lands inside
        the combo's own rect, and the list is already up when it is
        processed). The next click then resolves against this geometry. What
        is stored is used only to answer "which item index is under this
        point" — the emitted event still carries an index, never a
        coordinate."""
        if not self._is_combo_like(info):
            return
        inner = self._inner_expandable_combo(elem)
        if inner is None:
            return
        try:
            ecp = inner.GetCurrentPattern(self.EXPAND_COLLAPSE_PATTERN_ID)
            expanded = bool(ecp) and ecp.QueryInterface(
                self._mod.IUIAutomationExpandCollapsePattern
            ).CurrentExpandCollapseState == 1
        except Exception:
            return
        if not expanded:
            # The click collapsed an already-open list — the cache would
            # describe a list that is no longer on screen.
            self._dropdown_cache = None
            return
        rows = []
        for root in self._search_roots_for(elem):
            try:
                items = root.FindAll(7, self._uia.CreatePropertyCondition(
                    30003, self.CT_LIST_ITEM))
            except Exception:
                continue
            if not items or not items.Length:
                continue
            for i in range(items.Length):
                it = items.GetElement(i)
                try:
                    r = it.CurrentBoundingRectangle
                except Exception:
                    continue
                try:
                    nm = it.CurrentName or ""
                except Exception:
                    nm = ""
                rows.append(((r.left, r.top, r.right, r.bottom), nm))
            if rows:
                break
        if not rows:
            self._dropdown_cache = None
            return
        self._dropdown_cache = {"inner": inner, "rows": rows, "ts": time.time()}
        log(f"[inspect] dropdown opened — cached geometry of {len(rows)} items "
            "(the selecting click closes the list before it can be inspected)")

    @staticmethod
    def _rows_to_cache(rows):
        """[(element, rect)] -> [((left, top, right, bottom), name)]."""
        out = []
        for it, r in rows:
            try:
                nm = it.CurrentName or ""
            except Exception:
                nm = ""
            out.append(((r.left, r.top, r.right, r.bottom), nm))
        return out

    def _dropdown_item_from_cache(self, x, y):
        """Resolve (x, y) against the geometry cached while the list was open."""
        c = self._dropdown_cache
        if not c:
            return None
        if time.time() - c["ts"] > self.DROPDOWN_CACHE_TTL:
            self._dropdown_cache = None
            return None
        for i, ((left, top, right, bottom), nm) in enumerate(c["rows"]):
            if left <= x <= right and top <= y <= bottom:
                total = len(c["rows"])
                self._dropdown_cache = None      # consumed; the list is closed
                log(f"[inspect] live list already closed — ({x},{y}) matched the "
                    f"geometry cached while it was open: item {i + 1}/{total} "
                    f"(name={nm!r})")
                return c["inner"], i, total, nm
        return None

    def open_dropdown_item_at(self, elem, x, y):
        """A click at (x, y) that fell outside `elem`'s rect while `elem` is a
        combo: if an open dropdown item covers the point, return
        (inner_combo, index, total, item_name). Otherwise None.

        Tries every candidate root, not just the most specific one: the list
        can live in the app window's subtree, in its own top-level popup, or
        both (measured 2026-07-31 — PuTTY 5 items, HeidiSQL 5 and 18)."""
        scanned = []
        for root in self._search_roots_for(elem):
            try:
                items = root.FindAll(7, self._uia.CreatePropertyCondition(
                    30003, self.CT_LIST_ITEM))          # TreeScope_Subtree
            except Exception:
                continue
            if not items or not items.Length:
                scanned.append(0)
                continue
            rows = []
            for i in range(items.Length):
                it = items.GetElement(i)
                try:
                    r = it.CurrentBoundingRectangle
                    rows.append((it, r))
                except Exception:
                    continue
            scanned.append(len(rows))
            hit_idx = None
            for i, (it, r) in enumerate(rows):
                if r.left <= x <= r.right and r.top <= y <= r.bottom:
                    hit_idx = i
                    break
            if hit_idx is None:
                continue
            inner = self._inner_expandable_combo(elem)
            if inner is None:
                return None
            try:
                item_name = rows[hit_idx][0].CurrentName or ""
            except Exception:
                item_name = ""
            # The list is on screen RIGHT NOW, so this geometry supersedes
            # whatever was cached when it opened. Scrolling an open list moves
            # every item, and a stale cache then maps the next click to the
            # wrong index — measured 2026-08-03: after a scroll, the click that
            # picked the FIRST item (y=368) resolved against the pre-scroll
            # cache as item #5, so replay re-selected 'MySQL on RDS' instead of
            # going back to 'MariaDB or MySQL (TCP/IP)'. The scroll event
            # itself reads the live list, which is exactly when the cache can
            # be refreshed.
            self._dropdown_cache = {
                "inner": inner,
                "rows": self._rows_to_cache(rows),
                "ts": time.time(),
            }
            return inner, hit_idx, len(rows), item_name
        # No live list — the usual case for the click that SELECTS an item,
        # because that click closes the list before this runs. Fall back to
        # the geometry captured when it opened.
        cached = self._dropdown_item_from_cache(x, y)
        if cached is not None:
            return cached
        # DIAGNOSTIC — this path used to be silent, which is why the
        # 2026-08-03 mis-capture (dropdown item recorded as the CheckBox
        # behind the open list) gave no clue in the log about WHERE it broke.
        log(f"[inspect] open dropdown scan found no item under ({x},{y}) — "
            f"ListItem counts per search root: {scanned or 'no root resolved'}"
            f"; cached geometry: "
            f"{len(self._dropdown_cache['rows']) if self._dropdown_cache else 'none'}")
        return None

    # ── Owner-drawn popup menu items (HeidiSQL "더 보기") ────────────────────
    # Same problem as the owner-drawn combo above (icon-only items, no Name,
    # no AutomationId — the trigger click IS captured fine via RC-menuitem,
    # 2026-08-04 f981267, but the item it opens has nothing to select by).
    # Same three-mechanism answer, applied to Menu/MenuItem instead of
    # ComboBox/ListItem: menu_item_self() for a live direct hit,
    # snapshot_open_menu()/_menu_item_from_cache() for the item-picking click
    # that closes the popup before the worker thread inspects it, and
    # open_menu_item_at() for a live re-scan when the popup is still open but
    # the hit landed just outside a specific item's rect. Kept in a SEPARATE
    # cache (_menu_cache) from _dropdown_cache — a combo and a popup menu
    # cannot both legitimately be open at once, but a shared cache would
    # still couple two independent state machines for no reason.

    def snapshot_open_menu(self, elem, info, extra_pids=None):
        """Record the geometry of a popup menu's items WHILE IT IS STILL OPEN.

        Same race as snapshot_open_dropdown(): the click that picks an item
        also closes the popup, and the worker thread inspects 0.2-0.5s later
        — by then the Menu container and its MenuItem children are gone. So
        snapshot on the click that OPENS it (elem is the trigger MenuItem,
        already Expanded by the time this runs).

        extra_pids (2026-08-08): forwarded to _search_roots_for_menu() ->
        _same_process_top_windows() — pass the caller's authoritative,
        session-tracked PID set (Recorder._target_pids()) so candidate-window
        search doesn't depend solely on this specific elem's own (often
        hwnd=0) window handle or the momentarily-unreliable foreground
        window. See _same_process_top_windows()'s docstring for the FileZilla
        빠른 연결 지우기 case this fixes."""
        if not self._is_menu_like(info):
            # DIAGNOSTIC: only for elements that could plausibly BE a menu
            # trigger (support ExpandCollapsePattern) — avoids logging on
            # every ordinary click. Measured 2026-08-04 (HeidiSQL "더 보기"):
            # _is_menu_like() assumed the trigger reports controlType
            # "MenuItem" (mirroring how a ComboBox trigger reports
            # "ComboBox") — unconfirmed by any log, and if this trigger is
            # actually a VCL SplitButton/Button (plausible per CLAUDE.md's
            # own prose calling it a "SplitButton"), the guard silently
            # no-ops and the whole feature never engages. This makes that
            # mismatch visible instead of guessing at a fix.
            if self.has_expand_collapse(elem):
                log(f"[inspect] snapshot_open_menu: {info.get('name')!r} supports "
                    f"ExpandCollapsePattern but controlType="
                    f"{info.get('controlType')!r} != 'MenuItem' — not treated as "
                    "a menu trigger, no snapshot taken")
            return
        # Do NOT gate on ExpandCollapseState — measured 2026-08-04 (HeidiSQL
        # "더 보기", VCL SplitButton): across two full recordings, EVERY
        # single attempt logged ExpandCollapseState=None, even though the
        # popup was genuinely visibly open each time (a new top-level window
        # appears via the watcher right after the click). A SplitButton's
        # popup is shown via a raw TrackPopupMenu() call, which has no
        # obligation to keep this pattern's state property in sync the way a
        # real MenuItem does — gating on it made this feature dead code for
        # every SplitButton trigger. The reliable signal is simply "does a
        # Menu container exist right now among same-process top-level
        # windows" — _search_roots_for_menu() already answers that
        # independently of anything the trigger itself reports.
        rows = []
        tried = []
        # Retry budget for the SAME race osExpandCollapse.py's
        # resolve_target() already has (2026-08-04, HeidiSQL tab-switch ->
        # combo search): measured just now, "더 보기" clicked 7 times in one
        # recording found 0 Menu containers on same-process windows exactly
        # 4 times and 1 on the 3 successes — the popup window is real
        # (the watcher confirms it a beat later) but its accessibility
        # content isn't always populated yet at this worker thread's first
        # look. 5 attempts / 100ms (~0.5s budget) mirrors the scale of that
        # existing fix rather than inventing a new number.
        # 2026-08-05 (FileZilla 편집(E) -> 네트워크 구성 마법사 실측): 이 루프의
        # 비용 자체가 자신이 막으려던 레이스를 **일으키고** 있었다.
        # `FindAll(7=TreeScope_Descendants, ...)`는 그 창의 UIA 트리 전체를
        # 훑는다 — FileZilla 메인 창(자식 창만 80여 개)에서는 한 번에 ~240ms다.
        # 5회 재시도 × 창 2개 = 전체 트리 워크 10번 = **2.4초**. 실측 로그의
        # `[diag-click] ... gap=2.4103s`가 정확히 그 값이고, 그 2.4초 동안
        # 사용자는 이미 메뉴 항목을 골라버려서 팝업 창이 파괴됐다. 그래서
        # 검색 대상 목록에 팝업 hwnd가 아예 없었고("searched: ['hwnd=724316',
        # 'hwnd=1510346']" — 팝업 658780이 빠져 있음), 스냅샷이 실패했다.
        # 그 결과 항목 선택 클릭은 캐시도 라이브 메뉴도 못 찾아 그 아래
        # ToolBar로 잡혔고(id='5999' name=''), 재생 때 메뉴만 열리고 항목이
        # 선택되지 않아 "방화벽 및 라우터 설정 마법사" 창이 아예 안 떠서
        # 이후 모든 스텝이 window-not-found로 무너졌다.
        #
        # 해결: Win32 팝업 메뉴(TrackPopupMenu, 클래스 #32768)는 **자기 자신이
        # 최상위 창이고 그 창의 UIA 루트 요소가 곧 Menu**다. 따라서 트리를
        # 훑을 필요 없이 루트의 ControlType 속성 하나만 읽으면 된다(마이크로초
        # 단위). 이 프로젝트 범위의 앱은 전부 이 경로에 해당한다(FileZilla,
        # 7-Zip, HeidiSQL). 전체 트리 워크는 그 빠른 경로가 아무것도 못 찾았을
        # 때의 폴백으로만 남기고, 그마저도 첫 시도에서 한 번만 한다 — 재시도의
        # 목적은 "팝업이 아직 UIA에 안 올라왔다"를 기다리는 것이지 같은 값비싼
        # 스캔을 5번 반복하는 게 아니다(2026-08-04 HeidiSQL SplitButton 케이스는
        # 팝업 창은 있는데 그 **항목**이 아직 안 채워진 상황이라, 빠른 경로로
        # Menu 루트를 찾은 뒤 items가 빌 때 재시도하는 지금 구조로 그대로 커버된다).
        # 2026-08-05 (2차, 재녹화 3회 중 1회만 성공): 위 빠른 경로만으로는
        # 부족했다. 실측 gap — 성공 0.70s / 실패 1.36s·1.61s·1.87s·2.62s.
        # 실패 케이스의 공통점은 스냅샷이 도는 동안 **메뉴가 이미 파괴됐다**는
        # 것이고(watcher가 팝업과 마법사 창을 연달아 등록한 뒤에야 스냅샷이
        # 돌았다), 그 상태에서는 몇 번을 재시도해도 되살릴 수 없다. 그런데
        # 기존 예산(5회 × 최대 1회 deep scan)은 그 못 찾는 상황에서 1.4~2.6초를
        # 통째로 태웠고, 그 지연이 다음 이벤트로 전파돼(#8 1.87s -> #9 2.62s)
        # 워커가 점점 더 뒤처지는 악순환을 만들었다. 못 찾을 때의 비용에
        # 벽시계 상한을 둬서 그 전파를 끊는다 — 성공 케이스는 attempt 0의
        # 빠른 경로에서 즉시 끝나므로 이 상한에 영향받지 않는다.
        # 2026-08-05 (3차, 여전히 편집(E) 성공/파일(F) 실패로 갈림): 위
        # "루트별로 즉시 폴백" 순서 자체가 문제였다. 후보 순회가 한 루트씩
        # 순차 처리되는데, 그 루트에서 빠른 경로가 실패하면 **다음 루트의
        # 빠른 경로를 확인하기도 전에** 그 자리에서 바로 비싼 전체 스캔을
        # 돌렸다. 메인 창(hwnd=11406050)은 매번 첫 후보로 나오고, 거기엔
        # 실제 열린 메뉴가 아닌데도 "1 Menu container(s), 0 items"로 잡히는
        # 유령 Menu 서술자가 있다(메뉴바 자체의 정적 구조로 추정) — 이
        # 가짜 양성 하나를 스캔하는 데만 예산 대부분(gap=1.86s의 대부분)을
        # 썼고, 그 사이 진짜 살아있는 TrackPopupMenu 창은 다음 순번을
        # 기다리다 파괴됐다. 이 문제는 "파일(F)"(8개 항목, 메인 메뉴바)에서만
        # 나고 "편집(E)"(3개 항목)에서는 안 났는데 — 후보 순서상 편집 메뉴의
        # 진짜 팝업이 이 가짜 양성보다 먼저 열거됐을 뿐, 우연이다.
        #
        # 고침: 모든 후보 루트에 대해 **싼 빠른 경로만** 먼저 한 바�퀴 돈다
        # (속성 하나 읽기, 마이크로초 단위 — 전부 돌아도 총 비용이 무시할
        # 만하다). 그래도 못 찾았을 때만 비싼 전체 스캔을 후보별로 시도한다.
        # 이러면 살아있는 진짜 팝업이 후보 목록 어디에 있든, 죽은 지 오래인
        # 유령 서술자의 전체 트리 스캔보다 항상 먼저 확인된다.
        deadline = time.time() + 0.6
        for attempt in range(5):
            if attempt > 0:
                if time.time() > deadline:
                    tried.append(f"gave up after {attempt} attempt(s) — 0.6s budget spent")
                    break
                time.sleep(0.1)
            tried = []
            roots = list(self._search_roots_for_menu(elem, extra_pids=extra_pids))
            menu = None
            # 1단계: 모든 후보의 빠른 경로만 확인 (전체 트리 스캔 없음).
            for root in roots:
                try:
                    root_hwnd = root.CurrentNativeWindowHandle
                except Exception:
                    root_hwnd = None
                try:
                    if root.CurrentControlType == self.CT_MENU:
                        menu = root
                        tried.append(f"hwnd={root_hwnd}: root IS a Menu (fast path)")
                        break
                except Exception:
                    pass
                tried.append(f"hwnd={root_hwnd}: not a Menu root (fast path)")
            # 2단계: 첫 시도에서만, 그리고 1단계가 전부 실패했을 때만 전체
            # 트리 스캔으로 폴백한다(재시도는 "아직 안 올라왔다"를 기다리는
            # 것이지 같은 값비싼 스캔을 반복하는 게 아니다 — 위 주석 참고).
            if menu is None and attempt == 0:
                for root in roots:
                    try:
                        root_hwnd = root.CurrentNativeWindowHandle
                    except Exception:
                        root_hwnd = None
                    try:
                        menus = root.FindAll(7, self._uia.CreatePropertyCondition(
                            30003, self.CT_MENU))
                    except Exception as e:
                        tried.append(f"hwnd={root_hwnd}: FindAll raised {e!r}")
                        continue
                    menu_count = menus.Length if menus else 0
                    tried.append(f"hwnd={root_hwnd}: {menu_count} Menu container(s) (deep scan)")
                    if not menus or menus.Length != 1:
                        continue
                    cand = menus.GetElement(0)
                    try:
                        cand_items = cand.FindAll(7, self._uia.CreatePropertyCondition(
                            30003, self.CT_MENU_ITEM))
                    except Exception:
                        continue
                    if cand_items and cand_items.Length:
                        menu = cand
                        break
            if menu is None:
                continue
            try:
                items = menu.FindAll(7, self._uia.CreatePropertyCondition(
                    30003, self.CT_MENU_ITEM))
            except Exception:
                continue
            if items and items.Length:
                for i in range(items.Length):
                    it = items.GetElement(i)
                    try:
                        r = it.CurrentBoundingRectangle
                    except Exception:
                        continue
                    try:
                        nm = it.CurrentName or ""
                    except Exception:
                        nm = ""
                    rows.append(((r.left, r.top, r.right, r.bottom), nm))
                if rows:
                    break
            if rows:
                break
        if not rows:
            log(f"[inspect] snapshot_open_menu: {info.get('name')!r} was "
                f"Expanded but no Menu container with items was found — "
                f"searched: {tried or 'no root resolved'}")
            self._menu_cache = None
            self._menu_snapshot_fail_ts = time.time()
            return
        self._menu_cache = {"trigger": elem, "rows": rows, "ts": time.time()}
        log(f"[inspect] popup menu opened — cached geometry of {len(rows)} items "
            "(the selecting click closes the menu before it can be inspected)")

    def snapshot_new_popup_menu(self, new_hwnds, trigger):
        """Controltype/pattern-agnostic counterpart to snapshot_open_menu():
        given hwnds of top-level windows that just appeared (didn't exist
        right before this click), check whether any of them IS a Win32 popup
        menu (#32768, root ControlType == Menu) and, if so, cache its
        MenuItem children the same way snapshot_open_menu() does.

        Why this exists (2026-08-08, FileZilla 빠른 연결 드롭다운 실측via
        poc/probe_filezilla_quickconnect_dropdown.py): the trigger here is a
        plain Button, and both ExpandCollapsePattern and InvokePattern are
        unsupported/no-op on it (confirmed live — the popup only opens via a
        real SendInput click). snapshot_open_menu()'s _is_menu_like() gate
        (controlType in MenuItem/SplitButton) never lets this trigger's
        opening click reach the search at all, so the only chance left was
        the item-picking click — by which point the popup is already being
        destroyed (measured: `searched: ['hwnd=...: not a Menu root']` even
        after fixing the PID-derivation bug in _same_process_top_windows).

        This method is called unconditionally on EVERY click's inspection
        (see Recorder._inspect()), comparing the current same-process
        top-level window set against the previous click's — so it runs on
        the OPENING click (while the popup still reliably exists), not the
        closing one, regardless of what the trigger's own ControlType or
        pattern support claims."""
        for hwnd in new_hwnds:
            try:
                root = self._uia.ElementFromHandle(hwnd)
                if not root or root.CurrentControlType != self.CT_MENU:
                    continue
                items = root.FindAll(7, self._uia.CreatePropertyCondition(
                    30003, self.CT_MENU_ITEM))
            except Exception:
                continue
            if not items or not items.Length:
                continue
            rows = []
            for i in range(items.Length):
                it = items.GetElement(i)
                try:
                    r = it.CurrentBoundingRectangle
                except Exception:
                    continue
                rows.append((it, r))
            if not rows:
                continue
            self._menu_cache = {
                "trigger": trigger,
                "rows": self._rows_to_cache(rows),
                "ts": time.time(),
            }
            try:
                trigger_name = trigger.CurrentName if trigger is not None else None
            except Exception:
                trigger_name = "?"
            log(f"[inspect] snapshot_new_popup_menu: new window hwnd={hwnd} is a "
                f"Menu with {len(rows)} item(s) — cached (trigger={trigger_name!r})")
            return

    def _menu_item_from_cache(self, x, y):
        """Resolve (x, y) against the geometry cached while the menu was open."""
        c = self._menu_cache
        if not c:
            return None
        if time.time() - c["ts"] > self.DROPDOWN_CACHE_TTL:
            self._menu_cache = None
            return None
        for i, ((left, top, right, bottom), nm) in enumerate(c["rows"]):
            if left <= x <= right and top <= y <= bottom:
                total = len(c["rows"])
                self._menu_cache = None      # consumed; the menu is closed
                log(f"[inspect] live menu already closed — ({x},{y}) matched the "
                    f"geometry cached while it was open: item {i + 1}/{total} "
                    f"(name={nm!r})")
                return c["trigger"], i, total, nm
        return None

    def open_menu_item_at(self, elem, x, y):
        """A click at (x, y) that fell outside `elem`'s rect while `elem` is a
        menu trigger: if an open popup item covers the point, return
        (trigger, index, total, item_name). Otherwise None."""
        scanned = []
        for root in self._search_roots_for_menu(elem):
            try:
                menus = root.FindAll(7, self._uia.CreatePropertyCondition(
                    30003, self.CT_MENU))
            except Exception:
                continue
            if not menus or menus.Length != 1:
                scanned.append(0)
                continue
            try:
                items = menus.GetElement(0).FindAll(7, self._uia.CreatePropertyCondition(
                    30003, self.CT_MENU_ITEM))
            except Exception:
                continue
            if not items or not items.Length:
                scanned.append(0)
                continue
            rows = []
            for i in range(items.Length):
                it = items.GetElement(i)
                try:
                    r = it.CurrentBoundingRectangle
                    rows.append((it, r))
                except Exception:
                    continue
            scanned.append(len(rows))
            hit_idx = None
            for i, (it, r) in enumerate(rows):
                if r.left <= x <= r.right and r.top <= y <= r.bottom:
                    hit_idx = i
                    break
            if hit_idx is None:
                continue
            try:
                item_name = rows[hit_idx][0].CurrentName or ""
            except Exception:
                item_name = ""
            self._menu_cache = {
                "trigger": elem,
                "rows": self._rows_to_cache(rows),
                "ts": time.time(),
            }
            return elem, hit_idx, len(rows), item_name
        cached = self._menu_item_from_cache(x, y)
        if cached is not None:
            return cached
        log(f"[inspect] open menu scan found no item under ({x},{y}) — "
            f"MenuItem counts per search root: {scanned or 'no root resolved'}"
            f"; cached geometry: "
            f"{len(self._menu_cache['rows']) if self._menu_cache else 'none'}")
        return None

    def resolve_root_hwnd(self, elem, max_up=15):
        """Walk ControlView ancestors from `elem` until one with its own
        NativeWindowHandle is found, and return that hwnd (0 if none).

        describe()'s windowTitle currently trusts elem's OWN hwnd only, and
        falls back to GetForegroundWindow() when it's 0 (the common case for
        UIA leaf elements) — that fallback can silently produce a *correct*
        windowTitle for a *wrong* element (confirmed 2026-07-13: a PuTTY
        capture's very first click resolved to an unrelated 'Calculator' Edit
        element — bounding rect entirely outside the PuTTY window — while
        windowTitle still read 'PuTTY Configuration' because that happened to
        be the real foreground window at the time). This walk lets the caller
        verify the element's ACTUAL owning window against target_hwnds
        instead of trusting the foreground-window coincidence."""
        try:
            walker = self._uia.ControlViewWalker
            cur = elem
            for _ in range(max_up):
                if not cur:  # None or NULL COM pointer (CLAUDE.md §5)
                    return 0
                try:
                    h = cur.CurrentNativeWindowHandle
                    if h:
                        return h
                except Exception:
                    pass
                # 2026-08-08: was walker.GetParent(cur) — IUIAutomationTreeWalker
                # has no such method (only GetParentElement); every call here
                # raised AttributeError, silently swallowed by the try/except
                # below, so this always returned 0 for any hwnd=0 element that
                # needed more than one hop (e.g. a SplitButton) — confirmed via
                # a live FileZilla recording where this 0 sent _search_root_for
                # to GetForegroundWindow() (by then the newly-opened dialog, not
                # the app's own window), which made _refetch_ancestor_clean
                # search the wrong window's subtree and silently return None.
                cur = walker.GetParentElement(cur)
        except Exception:
            pass
        return 0

    # UIA_IsScrollPatternAvailablePropertyId — 스크롤 컨테이너 판별용.
    IS_SCROLL_PATTERN_AVAILABLE = 30034

    def scroll_container(self, elem):
        """Nearest self-or-ancestor exposing ScrollPattern — the container the
        generated osScroll.ps1 re-finds at replay time and scrolls
        programmatically (ScrollPattern first, PostMessage wheel fallback)."""
        try:
            walker = self._uia.ControlViewWalker
            cur = elem
            for _ in range(10):
                if cur is None:
                    return None
                try:
                    if cur.GetCurrentPropertyValue(self.IS_SCROLL_PATTERN_AVAILABLE):
                        return cur
                except Exception:
                    pass
                cur = walker.GetParentElement(cur)
        except Exception:
            pass
        return None

    def focused_element(self):
        return self._uia.GetFocusedElement()

    @staticmethod
    def describe(elem, fg_hwnd_hint=None, uia=None, root_hwnd_hint=None):
        """Extract metadata from a UIA element. Never raises - returns partial
        data on failure (reliability requirement).

        root_hwnd_hint (2026-08-08, 2nd fix): element_at() ALREADY computes
        a reliable owning-window hwnd for the RAW element via
        resolve_root_hwnd() before deepening to a more specific child
        (self._last_trace["root_hwnd"] — confirmed correct live: FileZilla's
        "파일(F)" click resolved root_hwnd=395052 there). The uia ancestor
        walk below re-derives this from the FINAL (deepened) elem instead,
        and — confirmed live — that re-derivation can fail even when the
        original walk on the raw element succeeded (deepened element's
        ancestor chain/COM behavior differs). Prefer this already-proven
        value first; it costs one GetAncestor+GetWindowText, no new COM
        tree-walk. Only meaningful for the ONE describe() call right after
        element_at() resolves this same click's primary element — other
        describe() calls in _inspect() (self-heal, ancestor lookups, cache
        hits) describe a DIFFERENT element and must not reuse this hint.

        uia (2026-08-08): the caller's IUIAutomation COM object. When elem's
        own hwnd is 0 (the common case — most menu items), windowTitle is
        resolved by walking elem's UIA ancestors for the nearest real window
        handle (same logic as UIAInspector.resolve_root_hwnd, duplicated
        here since describe() is a staticmethod) — this is STRUCTURAL, not
        focus-based, so it can't be fooled by which window happens to be
        foreground. This was resolve_root_hwnd()'s own stated purpose
        (2026-07-13, PuTTY: foreground fallback silently produced a correct
        windowTitle for the WRONG element) but was never actually wired into
        describe() — confirmed 2026-08-08 (FileZilla "파일(F)" menu click):
        even with a captured fg_hwnd_hint, GetForegroundWindow() read inside
        a low-level mouse hook can still reflect the PRE-click foreground
        (the OS hasn't finished switching focus to the just-clicked window
        yet), mislabeling windowTitle with an unrelated window (here: this
        very recording tool's own Chrome tab) — which then poisoned
        launchApp()'s target title and broke replay entirely.

        fg_hwnd_hint (2026-08-08): the foreground hwnd captured AT THE MOMENT
        of the physical click (see Recorder._on_click) — used only if the
        uia ancestor walk above also fails to find a real window. Kept as a
        second-best fallback before the live GetForegroundWindow() query."""
        info = {
            "automationId": "",
            "className": "",
            "name": "",
            "controlType": "",
            "windowTitle": "",
            "xpath": "",
            "hwnd": 0,
            "rootHwnd": 0,
            "locatorStrategy": "",   # NEW
            "locatorValue": "",      # NEW
            "nameSource": "",        # "" = real UIA CurrentName; otherwise which
                                      # fallback (below) scavenged the name from.
            "rect": None,            # DIAGNOSTIC: (left, top, right, bottom) of the
                                      # matched element — lets [click] log lines show
                                      # whether the click point actually falls inside
                                      # the returned element's bounds (UIA hit-test
                                      # bug) or not (coordinate-capture bug).
        }
        if elem is None:
            return info
        for key, getter in (
            ("automationId", lambda: elem.CurrentAutomationId),
            ("className", lambda: elem.CurrentClassName),
            ("name", lambda: elem.CurrentName),
        ):
            try:
                info[key] = getter() or ""
            except Exception:
                pass

        # Last-resort naming fallback: standard UIA Name/AutomationId are
        # both empty (measured on FileZilla's wxToolBar buttons, 2026-08-08 —
        # controlType='CheckBox', name='', automationId=''). Never runs when
        # either is already populated, so this cannot change behavior for
        # any element that already resolves normally. Order: LegacyIAccessible
        # (MSAA bridge — Name, then Description, then Help) before the plain
        # UIA HelpText/FullDescription properties, since Legacy is more often
        # populated by native Win32/MFC/VCL/wx controls that skip modern UIA
        # naming. Every attempt is wrapped so an unbound/mismatched comtypes
        # property name fails safely into the next fallback instead of
        # crashing describe() (its own "never raises" contract).
        if not info["automationId"] and not info["name"]:
            try:
                legacy = elem.GetCurrentPattern(UIAInspector.LEGACY_IACCESSIBLE_PATTERN_ID)
                if legacy:  # comtypes: NULL COM pointer on miss, not None — test truthiness
                    import comtypes.client  # local import (see UIAInspector.__init__ for the same pattern)
                    mod = comtypes.client.GetModule("UIAutomationCore.dll")  # cached by comtypes after first call
                    legacy = legacy.QueryInterface(mod.IUIAutomationLegacyIAccessiblePattern)
                    for src, getter in (
                        ("legacy-name", lambda: legacy.CurrentName),
                        ("legacy-description", lambda: legacy.CurrentDescription),
                        ("legacy-help", lambda: legacy.CurrentHelp),
                    ):
                        try:
                            val = getter() or ""
                        except Exception:
                            val = ""
                        if val:
                            info["name"] = val
                            info["nameSource"] = src
                            break
            except Exception:
                pass
            if not info["name"]:
                for src, getter in (
                    ("help-text", lambda: elem.CurrentHelpText),
                    ("full-description", lambda: elem.CurrentFullDescription),
                ):
                    try:
                        val = getter() or ""
                    except Exception:
                        val = ""
                    if val:
                        info["name"] = val
                        info["nameSource"] = src
                        break

        try:
            _ct_id = elem.CurrentControlType
            info["controlType"] = UIA_CONTROL_TYPES.get(_ct_id, str(_ct_id))
            # Raw numeric UIA ControlType id, additive alongside the
            # human-readable string above — _ancestor_sibling_selector's
            # cached_ct needs the numeric id (it feeds CreatePropertyCondition
            # directly), and describe() is the only place that already reads
            # CurrentControlType near click time (2026-08-08).
            info["controlTypeId"] = _ct_id
        except Exception:
            pass
        try:
            info["hwnd"] = elem.CurrentNativeWindowHandle or 0
        except Exception:
            pass
        try:
            r = elem.CurrentBoundingRectangle
            info["rect"] = (r.left, r.top, r.right, r.bottom)
        except Exception as e:
            # DIAGNOSTIC: keep the reason instead of silently swallowing it —
            # rect coming back None every single time (as opposed to
            # occasionally) means this is failing structurally, not per-element.
            info["rect"] = f"ERR:{type(e).__name__}:{e}"

        # Window title: walk up to the top-level window
        hwnd = info["hwnd"]
        try:
            if hwnd:
                root = ctypes.windll.user32.GetAncestor(hwnd, GA_ROOT)
                info["rootHwnd"] = root or hwnd
                info["windowTitle"] = win32gui.GetWindowText(root or hwnd)
        except Exception:
            pass
        # 2026-08-08 (2차 수정): element_at()가 원본(raw) 요소에서 이미 성공적으로
        # 계산해둔 root_hwnd(_last_trace["root_hwnd"])가 있으면 최우선으로 쓴다 —
        # 아래 조상 탐색을 "깊어진" 최종 elem에서 다시 하면 실측상 실패할 수 있다
        # (원본에서는 성공했던 걸). 새 COM 트리 탐색 없이 GetAncestor 한 번뿐.
        if not info["windowTitle"] and root_hwnd_hint:
            try:
                root = ctypes.windll.user32.GetAncestor(root_hwnd_hint, GA_ROOT) or root_hwnd_hint
                info["rootHwnd"] = root
                info["windowTitle"] = win32gui.GetWindowText(root)
            except Exception:
                pass
        # 2026-08-08: hwnd=0 요소(대부분의 메뉴 항목)는 포그라운드로 폴백하기
        # 전에, elem 자신의 UIA 조상을 걸어 올라가 진짜 소유 창을 구조적으로
        # 찾는다 — 포커스 상태와 무관하므로 타이밍에 흔들리지 않는다.
        # resolve_root_hwnd()와 같은 로직(설계 의도가 바로 이거였는데
        # describe()엔 실제로 연결된 적이 없었다 — 위 describe() docstring 참고).
        if not info["windowTitle"] and uia is not None and elem is not None:
            try:
                walker = uia.ControlViewWalker
                cur = elem
                for _ in range(15):
                    if not cur:  # None or NULL COM pointer (CLAUDE.md §5)
                        break
                    try:
                        h = cur.CurrentNativeWindowHandle
                        if h:
                            root = ctypes.windll.user32.GetAncestor(h, GA_ROOT) or h
                            info["rootHwnd"] = root
                            info["windowTitle"] = win32gui.GetWindowText(root)
                            break
                    except Exception:
                        pass
                    # 2026-08-08: same GetParent -> GetParentElement typo fix
                    # as resolve_root_hwnd() above (that one's comment has the
                    # full story) — this copy silently never climbed either.
                    cur = walker.GetParentElement(cur)
            except Exception:
                pass
        # Fallback: foreground window for UWP elements where hwnd=0
        # (UWP elements often return CurrentNativeWindowHandle=0, leaving
        #  windowTitle empty, causing buildUserPrompt to fall back to the
        #  English appName instead of the localized title like "계산기")
        if not info["windowTitle"]:
            try:
                fg = fg_hwnd_hint or ctypes.windll.user32.GetForegroundWindow()
                if fg:
                    info["windowTitle"] = win32gui.GetWindowText(fg)
            except Exception:
                pass

        try:
            # 2026-08-04 (TeamViewer WebView2 재생 무반응 원인): WebView2가
            # 그리는 요소는 전부 hwnd=0인 UIA-가상 하위요소다. GetAncestor(0,
            # GA_ROOT)는 0을 반환하므로 위 표현식이 통째로 0이 되고,
            # is_web_host(0)은 150행의 hwnd 가드에 걸려 무조건 False —
            # "hwnd가 없는 요소"를 감지하려는 기능이 hwnd가 없다는 바로 그
            # 이유로 항상 실패했다. windowTitle 폴백(위 1476-1482행)과 같은
            # 패턴으로 foreground window로 폴백한다.
            web_check_hwnd = (
                ctypes.windll.user32.GetAncestor(info.get("hwnd", 0) or 0, GA_ROOT)
                or info.get("hwnd", 0) or 0)
            if not web_check_hwnd:
                web_check_hwnd = ctypes.windll.user32.GetForegroundWindow() or 0
            info["isWebContent"] = is_web_host(web_check_hwnd)
        except Exception:
            info["isWebContent"] = False

        # NOTE: a popup MenuItem's AutomationId can be a volatile per-session
        # control-creation counter (measured 2026-08-04, HeidiSQL "더 보기"
        # overflow menu — see is_volatile_menuitem_id's history in git log for
        # the full measurement). That rejection intentionally does NOT happen
        # here: server.js's merge passes (mergeCrossWindowTriggerClicks) use
        # "does this event have ANY automationId/name at all" as the signal
        # for "this looks like a real interactive element worth pairing with
        # its trigger" — clearing it at capture time silently starved that
        # pairing and dropped the trigger click entirely (regression found
        # 2026-08-04 replaying the very fix meant to help this flow). The
        # volatility check now lives where the OTHER two id diseases
        # (isWindowHandleId, isRenderCounterId) already live — server.js's
        # selector builders — so structural "is there something here" and
        # "is this value trustworthy in a final selector" stay separate.

        # Locator strategy — explicit, so SYSTEM_PROMPT never guesses
        if info["automationId"]:
            info["locatorStrategy"] = "automationId"
            info["locatorValue"] = info["automationId"]
            info["xpath"] = f'//*[@AutomationId="{info["automationId"]}"]'
        elif info["name"]:
            info["locatorStrategy"] = "name"
            info["locatorValue"] = info["name"]
            info["xpath"] = f'//*[@Name="{info["name"]}"]'
        elif info["className"]:
            info["locatorStrategy"] = "className"
            info["locatorValue"] = info["className"]
            info["xpath"] = f'//*[@ClassName="{info["className"]}"]'
        elif info["controlType"]:
            info["locatorStrategy"] = "xpath"
            info["locatorValue"] = f'//*[@ControlType="{info["controlType"]}"]'
            info["xpath"] = info["locatorValue"]
        else:
            info["locatorStrategy"] = "coordinate"
            info["locatorValue"] = ""
            info["xpath"] = ""
        return info


# ----------------------------------------------------------------------------
# Window helpers
# ----------------------------------------------------------------------------
def is_aumid(s):
    """True if `s` is a UWP Application User Model ID ("PackageFamilyName!AppId")
    rather than a filesystem path. AUMIDs contain '!' and no path separators."""
    return "!" in s and "\\" not in s and "/" not in s


def pid_of_hwnd(hwnd):
    try:
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        return pid
    except Exception:
        return 0


PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def image_path_of_pid(pid):
    """Full exe path backing `pid`. Unlike window titles this is never
    localized, so it survives non-English Windows UI languages."""
    if not pid:
        return ""
    h = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return ""
    try:
        size = wintypes.DWORD(260)
        buf = ctypes.create_unicode_buffer(260)
        ok = ctypes.windll.kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size))
        return buf.value if ok else ""
    except Exception:
        return ""
    finally:
        ctypes.windll.kernel32.CloseHandle(h)


def match_keys_for_launch(exe_path, app_name):
    """Locale-independent keywords to look for in a window's owning-process
    image path. Window titles are localized (e.g. Korean '계산기'); package
    names and file paths are not, so this is the reliable signal for UWP
    launches where explorer.exe (not the tracked launch pid) actually spawns
    the real host process."""
    keys = set()
    if exe_path:
        if is_aumid(exe_path):
            # "PackageFamilyName_publisherHash!AppId" -> "PackageFamilyName"
            family = exe_path.split("!", 1)[0].split("_", 1)[0]
            k = re.sub(r'[^a-z0-9]', '', family.lower())
        else:
            base = re.sub(r'\.[^.]+$', '', os.path.basename(exe_path))
            k = re.sub(r'[^a-z0-9]', '', base.lower())
        if k:
            keys.add(k)
    if app_name:
        k = re.sub(r'[^a-z0-9]', '', app_name.lower())
        if k:
            keys.add(k)
    return keys


GW_OWNER = 4


def frame_owning_corewindow(core_hwnd, candidates):
    """Find the top-level window (from `candidates`) that has `core_hwnd` as
    an EnumChildWindows descendant.

    Probed and confirmed (2026-07-06 session): a UWP CoreWindow's parent,
    owner, AND GetAncestor(GA_ROOT) are all itself — there is NO upward
    Win32 link from CoreWindow to the ApplicationFrameWindow that actually
    receives clicks/keyboard input. The only real link is downward:
    EnumChildWindows(ApplicationFrameWindow) enumerates the CoreWindow as a
    child. So the CoreWindow must be found by scanning candidates, not by
    walking up from it."""
    for cand in candidates:
        if cand == core_hwnd:
            continue
        hit = [False]

        def _enum(child, _):
            if child == core_hwnd:
                hit[0] = True
                return False
            return True

        try:
            win32gui.EnumChildWindows(cand, _enum, None)
        except Exception:
            pass
        if hit[0]:
            return cand
    return 0


def probe_window(tag, hwnd):
    """One-shot diagnostic dump: class/pid/image/parent/owner/root/rect for
    `hwnd` and its immediate children. Used to establish the *actual* Win32
    relationship between the hwnd discovery locks onto and the hwnd real
    clicks route to, instead of guessing at WS_CHILD/GW_OWNER/GA_ROOT."""
    try:
        pid = pid_of_hwnd(hwnd)
        log(f"[probe:{tag}] hwnd={hwnd} cls='{win32gui.GetClassName(hwnd)}' "
            f"pid={pid} img='{image_path_of_pid(pid)}' "
            f"parent={win32gui.GetParent(hwnd)} "
            f"owner={win32gui.GetWindow(hwnd, GW_OWNER)} "
            f"root={ctypes.windll.user32.GetAncestor(hwnd, GA_ROOT)} "
            f"visible={win32gui.IsWindowVisible(hwnd)} "
            f"rect={win32gui.GetWindowRect(hwnd)}")
    except Exception as e:
        log(f"[probe:{tag}] hwnd={hwnd} FAILED: {e}")
        return

    # 2026-08-05: 자식 창을 한 줄씩 전부 찍으면 FileZilla 기준 녹화 1회당 78줄이
    # 나오는데, 그 78줄의 pid/img가 전부 동일하고 진단에 쓰인 적이 없다. 실제로
    # 사람이 로그를 복사해 붙여 넣는 워크플로에서는 이 덩어리가 전체의 절반
    # 가까이를 차지해 정작 봐야 할 [trace]/[inspect]/[diag-click] 줄을 파묻는다.
    # 클래스별 개수 요약(1줄)로 바꾸고, 전체 덤프는 AGENT_PROBE_VERBOSE=1일 때만
    # 낸다 — 클래스 구성(SysListView32/SysTreeView32/ComboBox 유무)이 이 덤프에서
    # 실제로 얻던 정보의 전부이고, 그건 요약으로 그대로 보존된다.
    verbose = os.environ.get("AGENT_PROBE_VERBOSE") == "1"
    counts = {}

    def _e(child, _):
        try:
            cls = win32gui.GetClassName(child)
            counts[cls] = counts.get(cls, 0) + 1
            if verbose:
                p = pid_of_hwnd(child)
                log(f"[probe:{tag}]   child={child} cls='{cls}' "
                    f"pid={p} img='{image_path_of_pid(p)}'")
        except Exception:
            pass
        return True

    try:
        win32gui.EnumChildWindows(hwnd, _e, None)
    except Exception:
        pass
    if counts and not verbose:
        total = sum(counts.values())
        summary = " ".join(f"{c}x{n}" for c, n in
                           sorted(counts.items(), key=lambda kv: -kv[1]))
        log(f"[probe:{tag}]   {total} child windows: {summary}"
            "   (set AGENT_PROBE_VERBOSE=1 for the per-child dump)")


def top_window_at(x, y):
    """Top-level window under a screen point.
    For UWP this is the ApplicationFrameWindow (GA_ROOT of the CoreWindow)."""
    try:
        child = ctypes.windll.user32.WindowFromPoint(wintypes.POINT(int(x), int(y)))
        if not child:
            return 0
        return ctypes.windll.user32.GetAncestor(child, GA_ROOT) or child
    except Exception:
        return 0


def tracked_window_containing(x, y, hwnds, margin=12):
    """First tracked hwnd whose window rect (expanded by `margin` px)
    contains the screen point.

    Used only as a contradiction detector: if top_window_at() says the point
    belongs to a foreign window while the point is geometrically inside a
    window we are actively recording, the two disagree and the event must not
    be dropped silently (2026-07-24 PuTTY: the click that switched to the
    Proxy panel vanished from the capture with no warning, so the generated
    test simply had no panel-switch step and every later step failed).

    margin (2026-08-08, FileZilla close-button 실측): a control sitting right
    at a window's edge (a titlebar close/X button is the common case) can
    have its real click coordinate land a few px past GetWindowRect()'s exact
    boundary — render/DPI/subpixel slop, measured 6-44px on FileZilla's own
    close button. Exact-boundary comparison made every such click fall all
    the way through to the harsher [skip] (fully dropped) path instead of
    even reaching the [skip-contradiction] (kept, selector preserved when
    independently confirmed — see _emit()) path. A small margin buys back
    the edge without loosening the check for a click that's genuinely
    elsewhere on screen.
    """
    checked = []
    for h in list(hwnds or []):
        try:
            vis = win32gui.IsWindowVisible(h)
            if not vis:
                checked.append(f"hwnd={h} visible=False")
                continue
            left, top, right, bottom = win32gui.GetWindowRect(h)
        except Exception as e:
            checked.append(f"hwnd={h} GetWindowRect raised {e!r}")
            continue
        hit = left - margin <= x < right + margin and top - margin <= y < bottom + margin
        checked.append(f"hwnd={h} rect=({left},{top},{right},{bottom}) hit={hit}")
        if hit:
            return h
    # 2026-08-08 진단: FileZilla 닫기 버튼이 명백히 창 경계 안(마진도 필요 없는
    # 좌표)인데도 이 함수가 0을 반환하는 사례가 실측됐다 — 순수 코드 리딩으로는
    # 원인(hwnd가 target_hwnds에 없었는지/rect가 달랐는지/visible=False였는지)을
    # 못 좁혀서 다음 실행에서 바로 확인할 수 있게 여기 남긴다.
    log(f"[tracked_window_containing] no match for ({x},{y}) margin={margin} — "
        f"checked: {checked or 'no hwnds passed'}")
    return 0


def foreground_top_window():
    try:
        fg = ctypes.windll.user32.GetForegroundWindow()
        if not fg:
            return 0
        return ctypes.windll.user32.GetAncestor(fg, GA_ROOT) or fg
    except Exception:
        return 0


def visible_toplevel_windows():
    """Set of currently visible top-level window handles."""
    found = set()

    def _enum(hwnd, _):
        try:
            if win32gui.IsWindowVisible(hwnd):
                found.add(hwnd)
        except Exception:
            pass
        return True

    try:
        win32gui.EnumWindows(_enum, None)
    except Exception:
        pass
    return found


def _window_has_size(hwnd):
    """True if hwnd's rect has non-zero width and height. Some VCL/Delphi
    apps (e.g. HeidiSQL) own a hidden 0x0 'TApplication' helper window that
    matches the launch PID instantly, well before the app's real visible
    form exists — this filters that out so discovery doesn't lock onto it."""
    try:
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        return (right - left) > 0 and (bottom - top) > 0
    except Exception:
        return False


# ----------------------------------------------------------------------------
# Recorder
# ----------------------------------------------------------------------------
class Recorder:
    def __init__(self):
        self.raw_queue = queue.Queue()
        self.recording = False
        self.event_count = 0
        self.session = {}            # appName, exePath, platform
        self.proc = None
        self.target_hwnds = set()    # top-level window handles owned by the target
        self._popup_hwnds = set()    # windows discovered by watcher (always treated as popups)
        # 2026-08-08: 가장 최근에 클릭 처리된 요소 — _watch_windows()가 새
        # Menu 팝업을 발견해 큐에 넣는 "popup_check" 이벤트가 이걸 트리거로
        # 재사용한다 (controlType/패턴 지원 여부와 무관하게 동작하는 팝업
        # 메뉴 감지 경로 — FileZilla 빠른 연결 드롭다운처럼
        # ExpandCollapsePattern/InvokePattern이 둘 다 무반응인 wx 커스텀
        # 트리거를 위한 것. 기존 snapshot_open_menu()는 _is_menu_like() 게이트
        # 때문에 이런 트리거의 여는 클릭에서 아예 시도되지 않았다). 트리거 없이
        # trigger=None으로 캐시하면 describe(None)이 빈 정보만 돌려줘 리플레이용
        # 셀렉터가 통째로 사라지므로, 반드시 실제 클릭 요소를 남겨둔다.
        self._last_clicked_elem = None
        # 2026-08-05: watcher가 새로 감지한 web-host(WebView2/Chromium) 창 중
        # 아직 접근성 트리 settle을 못 받은 것들. _watch_windows()는 COM 없이
        # win32gui만 쓰는 별도 스레드라 여기서 settled_subtree_count()를 직접
        # 부를 수 없다(§ "COM은 워커 스레드 하나에만") — 대신 여기 표시만 해두고
        # 워커 스레드의 _inspect()가 다음 클릭을 처리하기 전에 소비한다.
        self._pending_settle = set()
        # settle을 이미 마친 web-host hwnd. 같은 창을 두 번 기다리면 그 대기가
        # 첫 클릭을 2초 지연시켜, settle이 막으려던 바로 그 컨테이너-히트를
        # 스스로 유발한다(2026-08-05 TeamViewer 실측 — _watch_windows 주석 참고).
        self._settled_web_hosts = set()
        # hwnd -> 그 창을 처음 관측했을 때의 제목. 앱이 녹화 도중 창 이름을
        # 바꾸면(HeidiSQL 세션 관리자는 '신규'를 누르는 순간 ": Unnamed-N"이
        # 붙는다) 이후 모든 이벤트의 windowTitle이 재생 때 재현 불가능한
        # 값이 된다. session_meta 한 번만 찍는 것으로는 부족했다 — 실측
        # 2026-08-03: 앱이 뜨기 전에 녹화가 시작되면 창 탐색이 포그라운드
        # (도구 자신의 브라우저 창)로 폴백해서, 정작 진짜 앱 창은 그 뒤에
        # 워처가 찾는다. 그래서 창을 처음 볼 때마다 여기에 적어 두고
        # 이벤트마다 실어 보낸다.
        self._first_titles = {}
        self._pre_hwnds = set()      # visible top-levels snapshotted before launch
        # Wall-clock time _discover_target_windows() finished resolving
        # target_hwnds (real match or fallback). Mouse/keyboard hooks are live
        # from recording=True, i.e. BEFORE this resolves — any raw event
        # captured earlier (item["ts"] < this) was captured against a
        # not-yet-final target_hwnds (the app window may not even exist yet)
        # and is dropped in _emit(), regardless of when it's later processed.
        self._discovery_done_ts = 0.0
        self._probed_skip = False    # one-shot diagnostic probe on first mismatched-window click
        self._app_install_dir_cache = None   # lazily computed by _app_install_dir()

        self._mouse_listener = None
        self._kb_listener = None
        self._worker = None
        self._stop_flag = threading.Event()
        self._watcher = None          # background window-discovery thread

        # worker-side state
        self._last_left_click = None  # timing/pos of previous left click (dbl-click)
        # (x, y) of the most recent left click, independent of the
        # double-click window above — _flush_pending_click() nulls
        # _last_left_click on every keystroke (including the first one of a
        # type burst), so it can't be used to recover coords in
        # _flush_type_buffer. This one persists until overwritten by the next click.
        self._last_click_xy = None
        self._pending_scroll = None
        self._type_buffer = ""
        self._type_elem = None       # element info captured at first keystroke
        # Left-click emission is deferred from press to release so a
        # press-hold-move-release gesture can be told apart from a plain
        # click (drag support) — see _on_click/_handle/_emit_click_from_press.
        self._pending_press = None
        # 2026-08-10 (FileZilla '..' 단일 클릭 실측): 이름 없는(또는
        # automationId/className 둘 다 없는) ListItem/TreeItem 단일 클릭의
        # "click" 이벤트 방출을 ACTIVATION_CHECK_DELAY만큼 보류 — 그 사이
        # 실제로 뷰가 바뀌었는지(선택이 아니라 네비게이션/펼치기) 확인해
        # activatesOnSingleClick 플래그를 붙일지 정한다. _pending_scroll과
        # 같은 비차단 패턴(_flush_stale의 유휴 폴링에서 처리) — sleep 없음.
        self._pending_activation = None
        # rootHwndHex of the last emitted event — lets _emit() flag a
        # window-segment boundary (newWindowSegment) from ground-truth hwnd
        # identity instead of codegen re-deriving it from title diffing
        # downstream (2026-07-16, multi-window replay fix).
        self._last_emitted_hwnd_hex = ""

    # ---------------- control ----------------
    def start(self, app_name, exe_path, platform):
        if self.recording:
            return False, "Already recording"
        self.session = {"appName": app_name, "exePath": exe_path, "platform": platform}
        self.event_count = 0
        self.target_hwnds = set()
        self._popup_hwnds = set()
        self._pending_settle = set()
        self._settled_web_hosts = set()
        self._first_titles = {}
        self._probed_skip = False
        self._last_emitted_hwnd_hex = ""
        self._last_clicked_elem = None
        self._app_install_dir_cache = None

        # Snapshot visible top-level windows BEFORE launching, so discovery can
        # diff to find the new window(s) the target opens (locale-independent).
        self._pre_hwnds = visible_toplevel_windows()

        # Launch the target application. A UWP AUMID ("PackageFamilyName!AppId")
        # must be activated through the shell AppsFolder — launching the inner
        # WindowsApps exe directly is ACL-blocked, version-pinned, and skips UWP
        # activation. explorer shell:AppsFolder works even when not elevated.
        try:
            if is_aumid(exe_path):
                self.proc = subprocess.Popen(
                    ["explorer.exe", f"shell:AppsFolder\\{exe_path}"])
                log(f"Launched UWP {exe_path} via shell:AppsFolder")
            else:
                self.proc = subprocess.Popen([exe_path])
                log(f"Launched {exe_path} (pid={self.proc.pid})")
        except Exception as e:
            return False, f"Failed to launch '{exe_path}': {e}"

        self._stop_flag.clear()
        self.recording = True

        self._worker = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker.start()
        self._watcher = threading.Thread(target=self._watch_windows, daemon=True)
        self._watcher.start()

        # Hooks: callbacks ONLY enqueue raw data and return immediately,
        # so the OS input pipeline is never blocked.
        self._mouse_listener = mouse.Listener(on_click=self._on_click,
                                              on_scroll=self._on_scroll)
        self._kb_listener = keyboard.Listener(on_press=self._on_key)
        self._mouse_listener.start()
        self._kb_listener.start()
        log("Recording started")
        return True, "Recording started"

    def stop(self):
        if not self.recording:
            return False, "Not recording"
        self.recording = False
        # flush any in-flight typing before tearing down (requirement #1)
        self.raw_queue.put({"kind": "stop"})
        self._stop_flag.set()
        for l in (self._mouse_listener, self._kb_listener):
            try:
                if l:
                    l.stop()
            except Exception:
                pass
        if self._worker:
            self._worker.join(timeout=5)
        if self._watcher:
            self._watcher.join(timeout=2)
        log("Recording stopped")
        return True, "Recording stopped"

    # ---------------- hook callbacks (hot path - keep tiny) ----------------
    # pynput (win32 backend) passes an `injected` flag: True when the event was
    # synthesised (e.g. UWP buttons emit injected keystrokes on click, or an
    # automation tool sends input). We only record genuine physical input.
    def _on_click(self, x, y, button, pressed, injected=False):
        if not self.recording or injected:
            return
        # DIAGNOSTIC (A vs B investigation): GetCursorPos() read right here,
        # alongside pynput's own (x, y), to see if the two coordinate spaces
        # ever disagree at capture time.
        cursor_pt = wintypes.POINT()
        ctypes.windll.user32.GetCursorPos(ctypes.byref(cursor_pt))
        # 2026-08-08 (FileZilla "파일(F)" 실측): describe()의 windowTitle
        # 폴백(hwnd=0인 가벼운 요소 — 대부분의 메뉴 항목이 여기 해당)은
        # GetForegroundWindow()를 읽는데, 원래는 워커 스레드가 그 클릭을
        # 실제로 검사(_inspect)하는 시점에 "지금" 다시 조회했다. 워커 처리가
        # 지연되면(실측 gap=2.86초) 그 사이 사용자가 이미 다음 동작들을
        # 진행해 포그라운드가 바뀌어 있어, "클릭 당시"가 아니라 "뒤늦게 검사한
        # 시점"의 창을 windowTitle로 잘못 기록했다(파일(F) 클릭이 아직 열리지도
        # 않은 "사이트 관리자"로 찍힘 → launchFrag 오염 → launchApp이 존재한
        # 적 없는 창을 기다려 타임아웃). GetForegroundWindow()는 GetCursorPos()와
        # 같은 순수 win32 호출(COM/UIA 아님)이라 여기서 같이 읽어 큐에 실어
        # 보낸다 — describe()가 나중에 다시 조회하는 대신 이 값을 쓴다.
        fg_hwnd = ctypes.windll.user32.GetForegroundWindow()
        # Both press ("click") and release ("release") are enqueued — still
        # enqueue-only, no UIA/COM here. The worker pairs them to tell a plain
        # click apart from a press-hold-move-release drag (text selection).
        self.raw_queue.put({"kind": "click" if pressed else "release", "x": x, "y": y,
                            "cursor_x": cursor_pt.x, "cursor_y": cursor_pt.y,
                            "fg_hwnd_at_capture": fg_hwnd,
                            "button": button.name, "ts": time.time()})

    def _on_scroll(self, x, y, dx, dy, injected=False):
        log(f"[scroll-raw] x={x} y={y} dy={dy} injected={injected} recording={self.recording}")
        if not self.recording or injected:
            return
        cursor_pt = wintypes.POINT()
        ctypes.windll.user32.GetCursorPos(ctypes.byref(cursor_pt))
        self.raw_queue.put({"kind": "scroll", "x": x, "y": y,
                            "cursor_x": cursor_pt.x, "cursor_y": cursor_pt.y,
                            "dy": dy, "ts": time.time()})

    def _on_key(self, key, injected=False):
        if not self.recording or injected:
            return
        item = {"kind": "key", "ts": time.time(), "vk": getattr(key, "vk", None)}
        if isinstance(key, keyboard.KeyCode) and key.char is not None:
            item["char"] = key.char
        else:
            item["special"] = getattr(key, "name", str(key))
        self.raw_queue.put(item)

    # ---------------- target-app filtering (by top-level window handle) -------
    def _point_is_target(self, x, y):
        if not self.target_hwnds:
            return True  # discovery failed — do not silently drop everything
        top = top_window_at(x, y)
        if top in self.target_hwnds:
            return True
        # Title-based fallback: Electron and some UWP apps spawn child processes
        # whose hwnd->pid->exe chain doesn't match the launch pid. Accept and cache
        # the hwnd if the window title contains the app name (stripped to alnum).
        app_key = re.sub(r'[^a-z0-9]', '', self.session.get("appName", "").lower())
        if app_key and top:
            try:
                raw_title = win32gui.GetWindowText(top)
                title_key = re.sub(r'[^a-z0-9]', '', raw_title.lower())
                if title_key and (app_key in title_key or title_key in app_key):
                    self.target_hwnds.add(top)
                    log(f"[target] title-match hwnd={top} title='{raw_title}' accepted")
                    return True
            except Exception:
                pass
        return False

    def _foreground_is_target(self):
        if not self.target_hwnds:
            return True
        fg = foreground_top_window()
        if fg in self.target_hwnds:
            return True
        # UWP: the ApplicationFrameWindow that actually receives keyboard focus
        # is a different hwnd from the CoreWindow discovery tracks, and (unlike
        # the click path in _emit) this check has no lazy-frame adoption of its
        # own — so a type-only recording (no click ever happens) drops every
        # keystroke forever. Mirror _emit's lazy-frame adoption here: if fg
        # hosts a tracked CoreWindow as a child, adopt it now and let typing
        # through (confirmed 2026-07-08: Calculator typing-only capture was
        # silently empty because of this exact gap).
        if fg and fg not in self._popup_hwnds:
            for core in list(self.target_hwnds):
                if self._window_contains_child(fg, core):
                    self.target_hwnds.add(fg)
                    log(f"[target] lazy frame {fg} hosts CoreWindow {core} — added (keyboard)")
                    return True
            # Ordinary Win32 sibling/child dialog belonging to a tracked PID,
            # or a companion helper process living in the same install
            # directory (e.g. 7-Zip's 7zG.exe, 2026-07-21) — self-heal
            # immediately instead of waiting on _watch_windows()'s 0.5s poll,
            # which can otherwise drop every keystroke typed in that window
            # during the gap (2026-07-13).
            if self._owned_by_app(pid_of_hwnd(fg)):
                self.target_hwnds.add(fg)
                self._popup_hwnds.add(fg)
                log(f"[target] lazy self-heal {fg} — added (keyboard, "
                    "pre-empts 0.5s watcher poll; PID or same install dir)")
                return True
        return False

    def _target_pids(self):
        """PIDs already known to own a tracked hwnd, plus the launch PID —
        shared by _watch_windows()'s background poll and
        _foreground_is_target()'s immediate self-heal check so both agree on
        what counts as "belongs to this recording"."""
        launch_pid = self.proc.pid if self.proc else None
        pids = {launch_pid} if launch_pid else set()
        for hwnd in list(self.target_hwnds):
            p = pid_of_hwnd(hwnd)
            if p:
                pids.add(p)
        return pids

    def _app_install_dir(self):
        """Directory the launched exe lives in, lowercased, cached — or ''
        for UWP/AUMID launches (no meaningful filesystem directory)."""
        if self._app_install_dir_cache is not None:
            return self._app_install_dir_cache
        exe_path = self.session.get("exePath", "")
        d = "" if (not exe_path or is_aumid(exe_path)) else os.path.dirname(exe_path).lower().rstrip("\\/")
        self._app_install_dir_cache = d
        return d

    def _owned_by_app(self, pid):
        """True if `pid` is already PID-tracked, OR its exe lives in the
        SAME install directory as the launched app (2026-07-21, 7-Zip
        Benchmark repro: 7zG.exe is a genuinely separate helper process
        7zFM.exe spawns for long-running operations — different PID, so
        every existing PID-only check (this method's callers: _watch_windows
        popup poll, _inspect's self-heal, _emit's known-other-window gate)
        rejected its window forever, not just during the ~0.5s watcher-poll
        race. Directory match is a generic signal (works for any app that
        ships companion .exe helpers alongside the main one), not a 7-Zip
        special case."""
        if pid in self._target_pids():
            return True
        app_dir = self._app_install_dir()
        if not app_dir:
            return False
        img = image_path_of_pid(pid)
        return bool(img) and os.path.dirname(img).lower().rstrip("\\/") == app_dir

    def _settle_web_hosts(self, ins):
        """An embedded-Chromium app is not ready to be recorded the moment its
        window appears: its accessibility tree is still filling in, and a
        hit-test during that gap returns a container instead of the control
        under the cursor. Measured 2026-08-03 — TeamViewer's opening click
        captured as Group/AutomationId="root" covering 94% of the window,
        which made the whole replay unusable. Non-web apps skip this
        entirely, so nothing already-verified gets slower."""
        if ins is None:
            return
        for h in sorted(self.target_hwnds):
            if not is_web_host(h) or h in self._settled_web_hosts:
                continue
            root = ins.from_handle_safe(h)
            if not root:
                continue
            n = ins.settled_subtree_count(root)
            self._settled_web_hosts.add(h)
            self._pending_settle.discard(h)
            log(f"[target] web host hwnd={h} — accessibility tree settled "
                f"at {n} elements")

    def _discover_target_windows(self, ins=None):
        """Poll up to DISCOVER_TIMEOUT for the window(s) the launched app opens.
        Primary signal: top-level windows that appeared AFTER launch (diff vs
        the pre-launch snapshot). Also accept windows owned by the launch pid
        (classic Win32). Fallback: the current foreground top-level window."""
        launch_pid = self.proc.pid if self.proc else 0
        exe_path = self.session.get("exePath", "")
        app_name = self.session.get("appName", "")
        # UWP launches (AUMID, or classic exe names that Windows redirects to a
        # packaged app, e.g. calc.exe/notepad.exe) are actually spawned by
        # explorer.exe / a broker process, so launch_pid never matches the
        # real host window's owning pid. The window's TITLE is localized
        # (e.g. Korean '계산기'), so fuzzy-matching it against the English
        # appName silently fails on non-English Windows. The owning
        # process's image PATH is never localized, so match on that first.
        path_keys = match_keys_for_launch(exe_path, app_name)
        # Title fuzzy match — kept as a secondary fallback for windows whose
        # title happens to be in the same script as appName (English Windows).
        app_key = re.sub(r'[^a-z0-9]', '', app_name.lower())
        deadline = time.time() + DISCOVER_TIMEOUT
        best_found = None  # last zero-size-only match, used as a fallback
                            # if no real-sized window ever appears in time
        while time.time() < deadline and not self._stop_flag.is_set():
            current = visible_toplevel_windows()
            found = set()
            for hwnd in current:
                # window owned by the launched process (classic Win32) — trusted as-is
                if launch_pid and pid_of_hwnd(hwnd) == launch_pid:
                    found.add(hwnd)
                    continue
                # Path match applies to ANY visible window, pre-existing or
                # new: a single-instance app (e.g. Claude Desktop) that was
                # already running before this launch doesn't spawn a new
                # top-level window at all — AUMID activation just focuses the
                # existing one — so gating this on "not in _pre_hwnds" (as
                # the title fallback below still does) meant such apps could
                # never be discovered (confirmed 2026-07-06: zero candidates
                # considered, straight to foreground fallback). Path is
                # specific enough that pre-existing windows are safe to check.
                img_path = image_path_of_pid(pid_of_hwnd(hwnd))
                path_key = re.sub(r'[^a-z0-9]', '', img_path.lower())
                if path_keys and path_key and any(k in path_key for k in path_keys):
                    found.add(hwnd)
                    continue
                if hwnd in self._pre_hwnds:
                    continue
                # Fallback: fuzzy title match against appName. Restricted to
                # newly-appeared windows — loose text matching against a
                # pre-existing window (e.g. some unrelated app whose title
                # happens to contain the app name) is exactly the false-positive
                # risk _pre_hwnds was added to prevent.
                try:
                    raw_title = win32gui.GetWindowText(hwnd).strip()
                    if not raw_title:
                        continue
                    title_key = re.sub(r'[^a-z0-9]', '', raw_title.lower())
                    if app_key and title_key and (app_key in title_key or title_key in app_key):
                        found.add(hwnd)
                    else:
                        log(f"[target] ignoring unrelated new window hwnd={hwnd} title='{raw_title}' img='{img_path}'")
                except Exception:
                    pass
            # UWP: mouse/keyboard route to the top-level ApplicationFrameWindow
            # (GetAncestor(GA_ROOT) — owned by ApplicationFrameHost.exe, so it
            # never matches an app-specific path/title keyword on its own).
            # The CoreWindow matched above has no usable upward link to it
            # (parent/owner/GA_ROOT are all itself — probed and confirmed);
            # the only real link is downward, so scan visible top-levels for
            # whichever one has this CoreWindow as an EnumChildWindows child.
            for hwnd in list(found):
                try:
                    is_corewindow = win32gui.GetClassName(hwnd) == 'Windows.UI.Core.CoreWindow'
                except Exception:
                    is_corewindow = False
                if not is_corewindow:
                    continue
                frame = frame_owning_corewindow(hwnd, current)
                if frame and frame not in found:
                    found.add(frame)
                    log(f"[target] frame {frame} owns CoreWindow {hwnd} — added")
            # A zero-size candidate (e.g. HeidiSQL's hidden TApplication
            # window, rect (x,y,0,0)) isn't a usable target for session_meta
            # geometry/title — the real visible form appears a beat later as
            # a separate hwnd. Prefer sized candidates; if only zero-size
            # ones matched so far, remember them and keep polling for
            # something better within the same DISCOVER_TIMEOUT budget.
            sized = {h for h in found if _window_has_size(h)}
            if found and not sized:
                best_found = found
                time.sleep(0.2)
                continue
            if sized:
                found = sized
            if found:
                self.target_hwnds |= found
                titles = []
                for h in self.target_hwnds:
                    try:
                        titles.append(win32gui.GetWindowText(h))
                    except Exception:
                        titles.append("")
                log(f"[target] hwnds={self.target_hwnds} titles={titles}")
                for h in found:
                    probe_window("appwin", h)
                self._settle_web_hosts(ins)
                self._discovery_done_ts = time.time()
                return
            time.sleep(0.2)

        if best_found:
            self.target_hwnds |= best_found
            titles = []
            for h in self.target_hwnds:
                try:
                    titles.append(win32gui.GetWindowText(h))
                except Exception:
                    titles.append("")
            log(f"[target] hwnds={self.target_hwnds} titles={titles} "
                f"(zero-size fallback — no sized window appeared in time)")
            for h in best_found:
                probe_window("appwin", h)
            self._settle_web_hosts(ins)
            self._discovery_done_ts = time.time()
            return

        fg = foreground_top_window()
        if fg:
            # Verify the foreground window is plausibly the launched app
            # before trusting it — same path_keys/app_key signals the main
            # polling loop above already uses for every other candidate.
            # Measured 2026-08-04 (TeamViewer, already running so no new
            # window ever appeared within DISCOVER_TIMEOUT): this fallback
            # used to accept whatever was foreground UNCONDITIONALLY, and at
            # the exact moment discovery gave up, the user's OWN
            # control-panel Chrome tab was foreground (they'd just clicked
            # Launch in the browser) — so this tool's own UI got adopted as
            # a legitimate recording target. Chrome then satisfied every
            # later "is this hwnd tracked" check for the rest of the
            # session, a live instance of the exact self-capture class fixed
            # earlier today for PuTTY/HeidiSQL, just via a different entry
            # point. Rejecting an unmatched foreground window costs nothing
            # real: _watch_windows()'s background poll self-heals the actual
            # target via the same PID/install-dir signal within ~0.5s once
            # its window finally appears (WebView2 apps can take several
            # seconds to render — CLAUDE.md §4).
            fg_path = image_path_of_pid(pid_of_hwnd(fg))
            fg_path_key = re.sub(r'[^a-z0-9]', '', fg_path.lower())
            fg_title = win32gui.GetWindowText(fg)
            fg_title_key = re.sub(r'[^a-z0-9]', '', fg_title.lower())
            fg_matches = (
                (launch_pid and pid_of_hwnd(fg) == launch_pid)
                or (path_keys and fg_path_key and any(k in fg_path_key for k in path_keys))
                or (app_key and fg_title_key and (app_key in fg_title_key or fg_title_key in app_key))
            )
            if fg_matches:
                self.target_hwnds.add(fg)
                log(f"[target] fallback foreground hwnd={fg} title='{fg_title}'")
            else:
                log(f"[target] foreground hwnd={fg} title={fg_title!r} img={fg_path!r} "
                    f"does not match launched app (path_keys={path_keys}, "
                    f"app_key={app_key!r}) — NOT adopting as fallback target; "
                    "relying on watcher self-heal once the real window appears")
        else:
            log("[target] discovery failed — filtering disabled (accept all)")
        self._settle_web_hosts(ins)
        self._discovery_done_ts = time.time()

    def _watch_windows(self):
        """Background thread: poll for new top-level windows owned by target
        process PIDs (or a sibling helper process living in the same install
        directory, e.g. 7-Zip's 7zG.exe launched by 7zFM.exe for Benchmark —
        2026-07-21) and auto-add them to target_hwnds. Fixes the bug where
        popup/child windows opened after recording started were silently
        filtered by _point_is_target()."""
        while not self._stop_flag.is_set():
            try:
                for hwnd in visible_toplevel_windows():
                    if hwnd in self.target_hwnds:
                        continue
                    if self._owned_by_app(pid_of_hwnd(hwnd)):
                        self.target_hwnds.add(hwnd)
                        self._popup_hwnds.add(hwnd)
                        # 2026-08-05 (TeamViewer "세션 코드가 만료되었습니다"
                        # 대화상자 실측): 메인 창이 뜰 때는 _settle_web_hosts()가
                        # 접근성 트리가 찰 때까지 기다려주지만(2026-08-03 수정,
                        # CLAUDE.md §4), 녹화 도중 watcher가 감지하는 새 창(이
                        # 대화상자처럼)은 그 대상이 아니었다. 실측: 이 창이 뜨고
                        # 0.02초 만에 다음 클릭이 들어와 접근성 트리가 하나도
                        # 안 찬 상태에서 히트테스트가 통째로 Window 하나만
                        # 잡았다(원래 메인 창 버그와 동일한 증상). 워커 스레드가
                        # 소비할 수 있게 표시만 해둔다.
                        #
                        # 2026-08-05 (2차, TeamViewer "TeamViewer에 로그인" 실측
                        # — 위 1차 수정이 만든 회귀): watcher는 녹화 시작 시점에
                        # **메인 창도** 여기서 등록한다. 그런데 메인 창은 이미
                        # _discover_target_windows()가 _settle_web_hosts()로
                        # settle을 끝낸 상태다. 중복 판정이 없어서 _inspect()가
                        # 첫 클릭 직전에 같은 창을 한 번 더 settle 했고, 그
                        # 대기(settled_subtree_count의 quiet_for=1.5s 이상)가
                        # 첫 클릭을 2.09초 지연시켰다. 그 2초 사이에 사용자가
                        # 누른 버튼이 화면 전환을 끝내버려서, 뒤늦은 히트테스트가
                        # 전환된 화면의 root 컨테이너(창의 94%)를 잡았다 —
                        # 정확히 이 settle이 막으려던 그 증상을 스스로 만든 것.
                        # 이미 settle된 창은 다시 큐에 넣지 않는다.
                        if is_web_host(hwnd) and hwnd not in self._settled_web_hosts:
                            self._pending_settle.add(hwnd)
                        try:
                            title = win32gui.GetWindowText(hwnd)
                            self._remember_title(hwnd, title)
                            log(f"[watcher] added hwnd={hwnd} title='{title}'")
                        except Exception:
                            log(f"[watcher] added hwnd={hwnd}")
                        # 2026-08-08 (FileZilla 빠른 연결 드롭다운): 이 새 창이
                        # Win32 팝업 메뉴(#32768)일 수 있다 — 여기(watcher
                        # 스레드)는 win32gui만 쓰고 COM은 절대 안 만지므로(§
                        # "COM은 워커 스레드 하나에만"), 발견하는 이 순간 워커
                        # 큐에 확인 요청만 넣는다. 워커가 자기 차례에 COM으로
                        # 판별/스냅샷한다(_handle()의 popup_check 분기). 클릭
                        # 처리 시점에만 폴링하면 트리거 클릭 처리가 팝업 생성보다
                        # 먼저 끝나버리는 타이밍 공백이 있었다(2026-08-08 실측,
                        # 3차 — gap=0.03s로는 아직 팝업이 없었다). 워처가 이미
                        # 0.5초 주기로 팝업이 살아있는 동안 안정적으로 잡아내고
                        # 있었으므로, 그 발견을 워커가 그대로 소비하게 한다.
                        self.raw_queue.put({"kind": "popup_check", "hwnd": hwnd,
                                             "ts": time.time()})
            except Exception:
                pass
            time.sleep(0.5)

    # ---------------- worker (UIA lookups + emission happen here) ----------
    def _worker_loop(self):
        try:
            inspector = UIAInspector()
        except Exception:
            log("FATAL: could not initialise UI Automation")
            traceback.print_exc()
            return

        self._discover_target_windows(inspector)
        self._emit_session_meta()

        while True:
            try:
                item = self.raw_queue.get(timeout=QUEUE_POLL_TIMEOUT)
            except queue.Empty:
                self._flush_stale(inspector)
                if self._stop_flag.is_set() and self.raw_queue.empty():
                    break
                continue

            try:
                self._handle(item, inspector)
            except Exception:
                log("Error handling event (event kept where possible):")
                traceback.print_exc()

        # final flushes
        self._flush_type_buffer()
        self._flush_pending_click(inspector)
        self._flush_pending_scroll()
        log("Worker finished")

    def _handle(self, item, ins):
        kind = item["kind"]

        if kind == "stop":
            self._flush_type_buffer()
            self._flush_pending_click(ins)
            self._flush_pending_scroll()
            return

        if kind == "popup_check":
            # 2026-08-08: _watch_windows()가 새 same-PID 최상위 창을 발견한
            # 순간 넣은 마커 — COM을 가진 이 스레드에서 그 창이 Win32 팝업
            # 메뉴인지 판별하고, 맞으면 항목을 스냅샷해 _menu_cache에 넣는다.
            # trigger는 "가장 최근에 클릭 처리된 요소"(트리거 버튼) — 없으면
            # (녹화 시작 직후 등) describe(None)이 빈 정보만 줘서 캐시가
            # 무의미해지므로 스킵.
            if self._last_clicked_elem is not None:
                try:
                    ins.snapshot_new_popup_menu({item["hwnd"]}, self._last_clicked_elem)
                except Exception:
                    log("[popup_check] snapshot_new_popup_menu failed:")
                    traceback.print_exc()
            return

        if kind == "click":
            # focus moved -> typing into the previous field is complete
            self._flush_type_buffer()
            self._flush_pending_scroll()

            x, y, btn, ts = item["x"], item["y"], item["button"], item["ts"]
            # Position unified on GetCursorPos() (virtualized) coordinates,
            # not pynput's (physical) x,y — this process stays DPI-unaware,
            # so virtualized is what GetWindowRect() and every other
            # non-hook Win32 API on this process already agree on; pynput's
            # low-level hook was the odd one out. Inspect AND emit (stored/
            # replayed x,y) now both use the same (cx, cy) — no more split.
            cx = item.get("cursor_x", x)
            cy = item.get("cursor_y", y)

            if btn == "right":
                self._flush_pending_click(ins)
                self._emit_pointer_event("rightClick", cx, cy, ins, ts)
                return

            # Left press: emission is deferred to the matching release (see
            # "release" below) so a press-hold-move-release gesture can be
            # told apart from a plain click. A stale pending press with no
            # matching release (should not happen in practice — defensive
            # only) is flushed as a plain click first so it's never silently
            # dropped. Element inspection stays at press time — that's the
            # correct element for both a click and a drag's start point.
            if self._pending_press is not None:
                self._emit_click_from_press(self._pending_press, ins)
            # 2026-08-09 (사용자 지시, FileZilla+7-Zip 실측): 더블클릭의 두
            # 번째 서브클릭은 라이브 재조회를 하지 않는다 — 첫 클릭으로 UI가
            # 반응(선택 하이라이트, 리스트 재도장, 폴더 진입 등)하는 사이
            # hit-test 결과가 바뀌는 레이스가 실측됐다. "무엇을 클릭하려
            # 했는가"는 첫 서브클릭 시점에 이미 정해져 있으므로, 이 press가
            # 직전 완료된 클릭과 더블클릭으로 페어링될 조건(반경/시간)을 이미
            # 만족하면 그 클릭의 elem을 그대로 물려쓴다. 첫 서브클릭이 완전히
            # 못 찾은 "유령" 셀렉터(locatorStrategy=="coordinate")였다면
            # 물려주지 않고 평소처럼 라이브 재조회로 폴백한다.
            ll = self._last_left_click
            ll_elem = ll.get("elem") if ll else None
            ll_usable = bool(ll_elem) and ll_elem.get("locatorStrategy") != "coordinate"
            if (ll and ts - ll["ts"] <= DOUBLE_CLICK_INTERVAL
                    and abs(cx - ll["x"]) <= DOUBLE_CLICK_RADIUS
                    and abs(cy - ll["y"]) <= DOUBLE_CLICK_RADIUS
                    and ll_usable):
                elem = ll_elem
                com_elem = ll.get("com_elem")
                log(f"[diag-click] second sub-click of a double-click — reusing "
                    f"first sub-click's target (name={elem.get('name')!r}) "
                    f"instead of re-inspecting at ({cx},{cy})")
            else:
                elem = self._inspect(ins, cx, cy, fg_hwnd_hint=item.get("fg_hwnd_at_capture"))
                # 2026-08-10: _inspect()는 info 딕셔너리만 반환한다 — 활성화
                # 감지(아래 _emit_click_from_press/_pending_activation)에 필요한
                # 살아있는 COM 참조는 element_at()이 방금 채워둔
                # self._last_clicked_elem에서 얻는다(팝업 감지가 이미 같은
                # 용도로 쓰는 필드 재사용, 3.5xx행 근처). 콤보/메뉴 캐시-히트
                # 경로처럼 이 필드가 최신이 아닐 수도 있는 케이스는 아래
                # 후보 판정(controlType 등)이 대부분 걸러준다 — 최선 노력이지
                # 재생 셀렉터처럼 정확성을 요구하지 않는다(검증 실패 시 그냥
                # 플래그를 안 붙일 뿐).
                com_elem = self._last_clicked_elem
            # DIAGNOSTIC (kept for verification) — pynput_pt vs cursor_pt and
            # the resolved element name, so a re-recording can be eyeballed.
            gap = time.time() - ts
            delta = (x - cx, y - cy)
            log(f"[diag-click] pynput_pt=({x},{y}) cursor_pt=({cx},{cy}) "
                f"delta={delta} gap={gap:.4f}s "
                f"elem_name='{elem.get('name', '')}' elem_rect={elem.get('rect')}")
            self._pending_press = {"x": cx, "y": cy, "ts": ts, "elem": elem, "com_elem": com_elem}
            return

        if kind == "release":
            if item["button"] != "left":
                return  # right/middle releases carry no pending state
            press, self._pending_press = self._pending_press, None
            if press is None:
                return  # no matching press (e.g. recording started mid-press)
            x, y, ts = item["x"], item["y"], item["ts"]
            cx = item.get("cursor_x", x)
            cy = item.get("cursor_y", y)
            dist = math.hypot(cx - press["x"], cy - press["y"])
            if dist > DRAG_MIN_DIST:
                log(f"[diag-drag] start=({press['x']},{press['y']}) "
                    f"end=({cx},{cy}) dist={dist:.1f}")
                self._emit("drag", press["elem"], x=press["x"], y=press["y"],
                           ts=press["ts"], end=(cx, cy))
                self._last_left_click = None  # a drag breaks any double-click chain
            else:
                self._emit_click_from_press(press, ins)
            return

        if kind == "scroll":
            self._flush_type_buffer()
            self._flush_pending_click(ins)
            x, y, ts = item["x"], item["y"], item["ts"]
            cx = item.get("cursor_x", x)
            cy = item.get("cursor_y", y)
            if self._pending_scroll is None:
                elem_info = self._inspect(ins, cx, cy)
                # 스크롤 컨테이너 캡처 (2026-07-10 지시): 포인터 아래 요소에서
                # ScrollPattern 보유 조상까지 걸어 올라가 그 컨테이너의 셀렉터를
                # 기록 — 재생은 이 컨테이너를 다시 찾아 프로그래매틱으로 스크롤.
                target = None
                try:
                    cont = ins.scroll_container(ins.element_at(cx, cy))
                    if cont is not None:
                        d = UIAInspector.describe(cont)
                        target = {
                            "automationId": d.get("automationId", ""),
                            "className": d.get("className", ""),
                            "name": d.get("name", ""),
                            "controlType": d.get("controlType", ""),
                        }
                        log(f"[scroll] container id='{target['automationId']}' "
                            f"class='{target['className']}' name='{target['name'][:30]}'")
                except Exception:
                    pass
                self._pending_scroll = {"x": cx, "y": cy, "ts": ts,
                                        "amount": item["dy"],
                                        "elem": elem_info,
                                        "target": target}
            else:
                self._pending_scroll["amount"] += item["dy"]
                self._pending_scroll["ts"] = ts
            return

        if kind == "key":
            self._flush_pending_click(ins)
            self._flush_pending_scroll()
            special = item.get("special")
            char = item.get("char")
            vk = item.get("vk")

            if special == "tab":
                self._flush_type_buffer()
                return
            if special == "enter":
                # Preserve newline so sendKeys("...\n") can replay it. An
                # Enter with an empty buffer (blank line: Enter pressed right
                # after the previous flush) must still emit a newline-only
                # burst instead of vanishing — mirrors the burst-start gate/
                # elem-binding at lines 918-934 below (confirmed 2026-07-08:
                # consecutive blank lines in Notepad were silently dropped).
                if not self._type_buffer:
                    if not self._foreground_is_target():
                        self._flush_type_buffer()
                        return
                    elem = None
                    try:
                        fe = ins.focused_element()
                        elem = ins.describe(fe)
                    except Exception:
                        pass
                    self._type_elem = elem or {}
                self._type_buffer += "\n"
                self._flush_type_buffer()
                return
            if special == "backspace":
                self._type_buffer = self._type_buffer[:-1]
                return
            if special == "space":
                char = " "
            # Recover numpad keys that arrived without a .char (NumLock off makes
            # numpad digits report as navigation keys: up/down/end/...).
            if char is None and vk in NUMPAD_VK:
                char = NUMPAD_VK[vk]
            if char is None:
                log(f"[keydrop] special={special} vk={vk} — no char (modifier/nav)")
                return  # shift/ctrl/arrows etc. - ignored
            if not (0x20 <= ord(char) <= 0x7E):
                log(f"[keydrop] char={char!r} ord={ord(char)} vk={vk} — non-ASCII/IME")
                return  # non-ASCII (IME/CJK composition) — ignore silently

            # first keystroke of a burst: gate on the foreground window being
            # the target, then bind the buffer to the focused element.
            if not self._type_buffer:
                if not self._foreground_is_target():
                    log(f"[keydrop] char={char!r} fg={foreground_top_window()} "
                        f"— foreground not target {self.target_hwnds}")
                    return  # typing in another app -> ignore
                elem = None
                try:
                    fe = ins.focused_element()
                    elem = ins.describe(fe)
                except Exception:
                    pass
                # NOTE: we deliberately do NOT drop based on controlType here.
                # On calc the focused element while typing is the results Text,
                # not an Edit; sendKeys to the focused element/window still
                # replays the input. (Dropping by controlType was the reason
                # keyboard input on calc was previously lost entirely.)
                self._type_elem = elem or {}
            self._type_buffer += char
            return

    # UIA ControlType id for Edit — element_at() already uses the same literal
    # when it decides an unlabeled Edit is a list row's name cell.
    _UIA_EDIT_CT = 50004

    def _adopt_dead_row_cell(self, trace, raw, info, x, y):
        """Recover a list row whose element DIED before the second read.

        A softer form of the same race as _restore_recycled_row_name's: instead
        of the pooled row element being repointed at a different row, its
        provider goes away entirely. Measured 2026-08-06 (7-Zip, entering
        'hansung'):

            raw  = name='hansung' ct='Edit' id='' rect=(1000,406,1087,430)
            late = name=''        ct=''     id='' rect=ERR:COMError(-2147220991)

        Every field of the second read failed; the first read, taken before
        this click's own navigation, is the only surviving observation of what
        the user actually clicked — the same "earliest observation wins"
        principle used by dedupeDoubleClicks and by the light-dismiss dead
        element recovery further down _inspect().

        Two things kept the existing recoveries from covering it. The
        light-dismiss one is gated on `light_dismiss` (this element is not a
        scrim) and on picked_by starting with "raw-" (here element_at() had
        deepened, so picked_by was "smallest_element_at"). That picked_by test
        was protecting against adopting a large ANCESTOR when the pipeline had
        deliberately descended — which is a real concern, but it is really a
        question about raw's SIZE, not about which branch produced the pick.
        This checks the size directly instead.

        Why the recovered element is recorded as a ListItem rather than the
        Edit that raw literally reports: element_at() already encodes the rule
        that an Edit with no AutomationId inside this control family IS a list
        row's name-cell surrogate, and responds by climbing to
        _nearest_row_ancestor() (see its `is_unlabeled_edit` branch, and
        CLAUDE.md §5 — WinAppDriver's element/click on that surrogate is a
        silent no-op while Invoke() on the parent ListItem genuinely
        navigates). That climb is exactly what could not run here, because the
        element died first. Applying the codebase's own established rule to the
        last good observation is what keeps replay on the proven COM/ListItem
        path; recording it as an Edit would produce a step that replays
        cleanly and does nothing — the §3 false PASS this project forbids.

        Deliberately does NOT fire when raw is large: a window-filling
        container that happens to be named is exactly the "adopted the backdrop
        instead of the control" failure the light-dismiss guard was written for
        (FileZilla's log pane / status bar, 2026-08-05).
        """
        _r = info.get("rect")
        if isinstance(_r, tuple) and _r != (0, 0, 0, 0):
            return False                      # the late read is fine — not this case
        raw_rect = raw.get("rect")
        if not isinstance(raw_rect, tuple) or not point_in_rect(raw_rect, x, y):
            return False
        if raw.get("automationId") or raw.get("controlType") != "Edit":
            return False                      # not the unlabeled name-cell surrogate
        if (trace.get("root_hwnd") or 0) not in self.target_hwnds:
            return False                      # same self-contamination guard as below
        w, h = raw_rect[2] - raw_rect[0], raw_rect[3] - raw_rect[1]
        # 2026-08-10 (FileZilla local-list, w=490 rejected): 400 was tuned on
        # 7-Zip's narrower columns and over-rejects a wider single-column
        # name list. Widened rather than dropped — controlType=='Edit' above
        # already excludes containers (Pane/Window), so this height-paired
        # cap is only a sanity backstop against a degenerate/huge rect, not
        # the primary guard.
        if w <= 0 or h <= 0 or w > 1500 or h > 80:
            return False                      # a row's name cell, not a container
        info["name"] = raw.get("name") or ""
        info["className"] = raw.get("className") or ""
        info["automationId"] = ""
        info["controlType"] = "ListItem"
        info["rect"] = raw_rect
        info["hwnd"] = raw.get("hwnd") or 0
        log(f"[inspect] the element at ({x},{y}) died before it could be read "
            f"a second time (rect={_r!r}), but element_at()'s first read caught "
            f"it alive: an unlabeled Edit named {info['name']!r} at {raw_rect} "
            "— this control family's list-row name cell. Recording it as the "
            "ListItem row it belongs to, which is what the row-ancestor climb "
            "would have produced had the element survived.")
        return True

    def _adopt_stale_menuitem(self, trace, raw, info, x, y):
        """Recover a native popup-menu item whose second read landed on
        whatever opened AFTER the menu closed, not on a dead/empty read.

        A third shape of the same "read twice, world changed in between" race
        as _adopt_dead_row_cell, for Win32 menus instead of virtualized list
        rows. Measured live 2026-08-06 (FileZilla 파일(F) -> 사이트 관리자(S)...):
        the user's click landed 2.39s before the worker thread inspected it
        (a slow gap, [diag-click] gap=2.3880s) — long enough that by the time
        _inspect() re-described the same screen point, the menu had closed
        AND the Site Manager dialog it opens had already appeared there:

            raw  = name='사이트 관리자(S)...\\tCtrl+S' ct='MenuItem' id='33662'
                   rect=(521,130,953,156)
            late = name=''                              ct='SplitButton' id=''
                   rect=(520,128,571,159)

        Unlike the ListView case, `late` here is not dead (COMError/all-zero
        rect) — it is a perfectly valid, live element. It is simply the WRONG
        one: a toolbar SplitButton belonging to the dialog that opened at
        that exact screen position after the click, not the menu item the
        user actually selected. _menu_item_from_cache() exists for exactly
        this race but only matches while its own geometry snapshot is still
        current; a gap this long can outlast it. _adopt_dead_row_cell() does
        not apply either — it requires the late read to have failed and the
        raw read to be an unlabeled Edit surrogate, neither of which holds.

        Consequence when this goes unrepaired: the event that should select
        "사이트 관리자(S)..." from the 파일(F) menu gets an empty name AND
        automationId, codegen has nothing to build a selector from, and drops
        the click. The dialog that click was supposed to open never opens at
        replay, and every later step scoped to that window fails
        ("window ... not found") — one uncaptured click cascading into total
        failure for the rest of the recording.

        Deliberately narrow: only a MenuItem raw read with BOTH a name and an
        automationId (a resolvable, selector-worthy item — not an icon-only
        one) is restored, and only when the click point still falls inside
        raw's own rect and raw's root window is one this session is tracking.
        A control that genuinely differs between reads for a real reason
        (not this race) is not something this function can distinguish from
        one that raced, so it is intentionally scoped to the one measured
        shape rather than generalized to "raw differs from late, adopt raw."
        """
        if raw.get("controlType") != "MenuItem":
            return False
        if not raw.get("name") or not raw.get("automationId"):
            return False
        raw_rect = raw.get("rect")
        if not isinstance(raw_rect, tuple) or not point_in_rect(raw_rect, x, y):
            return False
        if (trace.get("root_hwnd") or 0) not in self.target_hwnds:
            return False
        late_name = info.get("name") or "(unnamed)"
        info["name"] = raw["name"]
        info["automationId"] = raw["automationId"]
        info["controlType"] = "MenuItem"
        info["className"] = raw.get("className") or ""
        info["rect"] = raw_rect
        log(f"[inspect] menu item at ({x},{y}) was stale: the second read landed "
            f"on {late_name!r} from whatever opened after the menu closed, but "
            f"the first read — taken while the menu was still open — saw "
            f"{raw['name']!r}. Restoring the earlier identity.")
        return True

    def _restore_recycled_row_name(self, ins, info, x, y):
        """Undo a virtualized list row's identity being recycled mid-inspection.

        A virtualized ListView (7-Zip's SysListView32, and the same pattern in
        every other Win32 report view) does not create one UIA element per row.
        It keeps a small pool and REPOINTS those elements at different rows as
        the list scrolls or reloads. The element stays alive the whole time —
        it simply starts reporting a different Name.

        That collides with the fact that this pipeline reads a click's element
        TWICE: element_at() describes the raw hit-test result, then climbs to
        the row and returns a live COM pointer, and _inspect() describes that
        pointer again. Between the two reads sits the navigation the user's own
        double-click just triggered. Measured 2026-08-06 (7-Zip, presses
        back-computed from the emit timestamps and the [diag-click] gap):

            event   element_at()'s 1st read   _inspect()'s 2nd read
            #4      'C:'                      '$Recycle.Bin'
            #10     'project'                 '.code-review-graph'

        In BOTH cases the first read was the truth — it is what the user
        actually double-clicked — and the second read had already slid onto the
        row that the navigation put under the cursor afterwards. The worker was
        362ms and 225ms behind the press respectively, which is longer than
        7-Zip takes to repaint, so even the FIRST of a double-click's two
        presses inspected post-navigation.

        Why this is not dedupeDoubleClicks' problem: that function already
        picks the earliest constituent click of a trio, and that rule is
        correct. It just cannot help when the value ON the earliest click was
        already corrupted before the server ever saw it. Fixing it here keeps
        one rule ("the earliest observation is the pre-navigation one") in one
        place instead of two competing ones.

        Deliberately NOT a wholesale swap to the raw element. raw is the narrow
        inner name CELL (rect 1000..1041 vs the row's 996..1756), and CLAUDE.md
        §5 records that WinAppDriver's element/click on that Edit surrogate is
        a silent no-op while Invoke() on the parent ListItem genuinely
        navigates — adopting it would trade this bug for that one. The climbed
        row is the structurally correct element; only its Name rotted, so only
        its Name is restored.

        Fires only when all of these hold, so a capture where nothing was
        recycled (first read == second read) is bit-for-bit unaffected:
          - element_at() ended on the row-ancestor climb
          - the raw first read had a name, and it differs from the late one
          - the click point falls inside the raw element's rect
          - the raw element's rect sits inside the adopted row's rect, i.e.
            raw really is a cell OF this row and not something unrelated
        """
        trace = getattr(ins, "_last_trace", None) or {}
        raw = trace.get("raw_info") or {}
        raw_name = raw.get("name") or ""
        late_name = info.get("name") or ""
        if not raw_name or raw_name == late_name:
            return
        if trace.get("picked_by") != "row-ancestor":
            if self._adopt_dead_row_cell(trace, raw, info, x, y):
                return
            if self._adopt_stale_menuitem(trace, raw, info, x, y):
                return
            # 2026-08-10 (FileZilla, picked_by='smallest_element_at'): late
            # is a perfectly live, valid element here — just the wrong one,
            # because the element at this exact screen position was
            # recycled/repointed between the two reads. Same "earliest
            # observation wins" principle as the row-ancestor branch below,
            # generalized to any picked_by: if raw and the adopted element
            # sit at (within a few pixels of) the same position, only the
            # Name is provably stale — restore just that, and leave the
            # click target itself (info's rect/controlType) untouched.
            raw_rect = raw.get("rect")
            late_rect = info.get("rect")
            if (rects_close(raw_rect, late_rect)
                    and point_in_rect(raw_rect, x, y)):
                info["name"] = raw_name
                if not info.get("automationId") and raw.get("automationId"):
                    info["automationId"] = raw["automationId"]
                log(f"[inspect] element at ({x},{y}) (picked_by="
                    f"{trace.get('picked_by')!r}) was recycled between "
                    f"element_at()'s read and this one: it now reports "
                    f"{late_name!r} but the first read — taken before this "
                    f"click's own navigation — saw {raw_name!r} at the same "
                    f"position (raw_rect={raw_rect!r} late_rect="
                    f"{late_rect!r}). Restoring the earlier name; the "
                    "element itself (rect/controlType) is kept as-is.")
                return
            # None of the known shapes — leave it alone, but print the two
            # facts that would decide how to handle it if it ever shows up.
            log(f"[inspect] identity rot at ({x},{y}) NOT repaired: "
                f"picked_by={trace.get('picked_by')!r} "
                f"raw=[name={raw_name!r} ct={raw.get('controlType')!r} "
                f"id={raw.get('automationId')!r} rect={raw.get('rect')!r}] "
                f"late=[name={late_name!r} ct={info.get('controlType')!r} "
                f"id={info.get('automationId')!r} rect={info.get('rect')!r}]")
            return
        raw_rect, row_rect = raw.get("rect"), info.get("rect")
        if not isinstance(raw_rect, tuple) or not isinstance(row_rect, tuple):
            return
        if not point_in_rect(raw_rect, x, y):
            return
        if not (row_rect[0] <= raw_rect[0] and row_rect[1] <= raw_rect[1]
                and raw_rect[2] <= row_rect[2] and raw_rect[3] <= row_rect[3]):
            return
        info["name"] = raw_name
        if not info.get("automationId") and raw.get("automationId"):
            info["automationId"] = raw["automationId"]
        log(f"[inspect] row at ({x},{y}) was recycled between element_at()'s "
            f"read and this one: it now reports {late_name!r} but the first "
            f"read — taken before this click's own navigation — saw "
            f"{raw_name!r}. Restoring the earlier name; the row element "
            "itself (rect/controlType) is kept as-is.")

    def _inspect(self, ins, x, y, fg_hwnd_hint=None):
        # 2026-08-11 (FileZilla 체크박스 / Notepad 탭닫기 실측, 이전 클릭
        # 15~45ms 뒤): info를 참조하는 except 블록보다 먼저 예외가 나면
        # (info가 3719/3736행 등에서 아직 한 번도 할당되기 전) except에서
        # `info`를 그냥 읽으면 UnboundLocalError가 난다 — 항상 바인딩되게.
        info = None
        try:
            # 2026-08-05 (TeamViewer "세션 코드가 만료되었습니다" 대화상자
            # 실측): watcher가 감지한 새 web-host 창은 여기서 소비한다 —
            # _watch_windows()는 COM이 없는 별도 스레드라 직접 못 기다린다
            # (표시만 해둠, 위 __init__/_pending_settle 주석 참고). 이 다음
            # 클릭이 그 창을 히트테스트하기 전에 접근성 트리가 찰 때까지
            # 기다려, 메인 창이 뜰 때 이미 한 번 고친 것과 같은 레이스
            # (Group/AutomationId="root"로 통째로 잡히는 문제, CLAUDE.md §4)
            # 가 나중에 뜨는 창에서 재발하지 않게 한다.
            if self._pending_settle:
                pending, self._pending_settle = self._pending_settle, set()
                for h in pending:
                    # 이미 settle된 창은 건너뛴다 — 중복 대기가 첫 클릭을
                    # 2초 지연시켜 캡처를 망가뜨린 회귀가 있었다(위 참고).
                    if h in self._settled_web_hosts:
                        continue
                    try:
                        root = ins.from_handle_safe(h)
                        if root:
                            n = ins.settled_subtree_count(root)
                            self._settled_web_hosts.add(h)
                            log(f"[inspect] settled new web-host hwnd={h} "
                                f"at {n} elements before hit-testing")
                    except Exception:
                        pass
            # An item-PICKING click (as opposed to the click that opens the
            # list) closes the list before this runs, so its own hit test is
            # never going to succeed live — the entire reason
            # _dropdown_item_from_cache()/_menu_item_from_cache() exist.
            # Position resolved from a snapshot taken while the list was
            # genuinely open and confirmed to belong to this app is
            # trustworthy independent of this click's own hwnd, so this
            # answers the question outright: on a hit, every value below is
            # overwritten by the trigger's own describe() and returned
            # immediately.
            #
            # 2026-08-05 (사용자 보고 "클릭 인지가 느리다", FileZilla 실측):
            # 이 블록은 원래 element_at()+describe() 뒤에 있었다 — 그 결과
            # **버려질 것이 확실한** 전체 트리 탐색(ElementFromPoint →
            # smallest_element_at → _deepen)을 매번 먼저 지불했다. 측정된
            # [diag-click] gap: 일반 클릭 0.04~0.09s인데 캐시로 풀리는 메뉴
            # 항목 클릭이 0.94s / 0.99s — 20배 차이가 전부 이 낭비다.
            # 캐시 조회는 순수 기하 비교라 비용이 사실상 0이므로, 맞으면
            # 트리 탐색 자체를 건너뛴다. 캐시가 빗나가면 종전 경로 그대로
            # 진행하므로 판정 로직은 하나도 바뀌지 않는다.
            cache_hit = ins._dropdown_item_from_cache(x, y)
            cache_kind = "combo"
            if cache_hit is None:
                cache_hit = ins._menu_item_from_cache(x, y)
                cache_kind = "menu"
            if cache_hit is not None:
                trigger, idx, total, item_name = cache_hit
                info = ins.describe(trigger, fg_hwnd_hint=fg_hwnd_hint, uia=ins._uia)
                if cache_kind == "combo":
                    info["comboItemIndex"] = idx
                    info["comboItemCount"] = total
                    info["comboItemName"] = item_name
                else:
                    info["menuItemIndex"] = idx
                    info["menuItemCount"] = total
                    info["menuItemName"] = item_name
                info["expandCollapse"] = True
                return info
            elem = ins.element_at(x, y)
            # 2026-08-08 (2차 수정): element_at()가 원본 요소에서 이미 계산해둔
            # 신뢰성 있는 root_hwnd — 아래 primary describe() 호출에만 전달
            # (다른 8개 describe() 호출은 각각 다른 요소를 기술하므로 이 값을
            # 재사용하면 안 됨 — describe()의 root_hwnd_hint docstring 참고).
            root_hwnd_hint = ins._last_trace.get("root_hwnd")
            info = ins.describe(elem, fg_hwnd_hint=fg_hwnd_hint, uia=ins._uia, root_hwnd_hint=root_hwnd_hint)
            # 2026-08-08 (FileZilla 빠른 연결 드롭다운 실측, 3차 수정): 트리거의
            # controlType/패턴 지원 여부와 무관하게 새 Menu 팝업을 잡아내는
            # 감지는 여기(클릭 처리 시점)가 아니라 _watch_windows()가 새 창을
            # 발견하는 순간 큐에 넣는 "popup_check" 이벤트로 처리한다(아래
            # _handle()의 popup_check 분기, _watch_windows() 참고) — 클릭
            # 처리 시점에만 폴링하면 트리거 클릭 처리가 팝업 생성보다 먼저
            # 끝나버리는 타이밍 공백이 있었다(실측: gap=0.03s로 폴링해도 그
            # 순간엔 아직 팝업이 없었다). 여기서는 "가장 최근에 클릭한 요소"만
            # 기억해 popup_check가 트리거로 재사용할 수 있게 한다.
            if elem is not None:
                self._last_clicked_elem = elem
            # 2026-08-05 (7-Zip "hansung" 실측): describe()가 방금 위에서 이미
            # 성공적으로 읽은 controlType을 여기서 독립적으로 재조회하는데,
            # 그 사이 요소가 죽으면(자기 클릭이 유발한 화면 전환) 이 필드만
            # 조용히 빈 문자열로 남는다 — name은 describe() 안에서 controlType
            # 보다 먼저 읽혀 이미 성공한 채로 남을 수 있어(각 필드 별도
            # try/except), "이름은 있는데 타입은 없는" 반쪽짜리 정보가 만들어
            # 진다. _nearest_row_ancestor()가 이 요소를 골라내며 이미 확인해둔
            # 값이 있으면 그걸로 메운다 — 새 COM 호출 없이, 이미 검증된 사실을
            # 복원할 뿐이다.
            if not info.get("controlType"):
                confirmed = ins._last_trace.get("confirmedRowControlType")
                if confirmed:
                    info["controlType"] = confirmed
                    log(f"[inspect] controlType re-read failed for a dying "
                        f"element (name={info.get('name')!r}) — restored "
                        f"'{confirmed}' from the row-ancestor climb that "
                        "found it moments earlier, alive")
            self._restore_recycled_row_name(ins, info, x, y)
            # DIAGNOSTIC: element_at()'s full decision path in one line, plus
            # both window-identity signals (top_window_at: Win32 hit test at
            # this point; foreground_top_window: GetForegroundWindow) and the
            # current tracked-window sets. [diag-click] below only shows the
            # FINAL adopted element, which looks identical whether it came
            # from a correct hit-test or a wrong-window fallback — this line
            # is what actually distinguishes them.
            try:
                tr = ins._last_trace
                log(f"[trace] pt=({x},{y}) picked_by={tr.get('picked_by')} "
                    f"raw=[{tr.get('raw')}] root_hwnd={tr.get('root_hwnd')} "
                    f"root_ok={tr.get('root_ok')} top_win={top_window_at(x, y)} "
                    f"fg={foreground_top_window()} "
                    f"targets={sorted(self.target_hwnds)} "
                    f"popups={sorted(self._popup_hwnds)}")
            except Exception as e:
                log(f"[trace] failed: {e}")
            light_dismiss = info.get("automationId") == "Light Dismiss"
            # 2026-08-11 (FileZilla "새 북마크" 체크박스 실측, STEP27): set
            # below when THIS click's self-heal is accepted while a recent
            # snapshot_open_menu() failure (menu-close/dialog-open capture
            # race) is still fresh — read later when building the
            # ancestor+index selector to flag the event as ambiguous instead
            # of silently emitting a selector that can resolve to the wrong
            # sibling. One-shot, consumed at the end of this call.
            ambiguous_self_heal = False
            # NOTE: the dropdown/menu item cache is consulted at the TOP of
            # this function now (see the comment there) — an item click is
            # typically hwnd==0 (UIA-only sub-element) like its trigger, so
            # it would otherwise get light_dismiss=True immediately and never
            # reach the open_dropdown_item_at()/open_menu_item_at() branches
            # later in this function (those require not light_dismiss too) —
            # measured 2026-08-04 (HeidiSQL): items landed on a
            # cached-but-unreachable path and fell through to whatever the
            # closed list had been covering.
            # Resolved element belongs to a window this recording isn't
            # tracking at all (confirmed 2026-07-13: a PuTTY capture's very
            # first click — right after window discovery — resolved to an
            # unrelated 'Calculator' Edit element whose bounding rect sat
            # entirely outside the PuTTY window; describe()'s windowTitle
            # still read 'PuTTY Configuration' only because that happened to
            # be the real foreground window at that instant, masking the
            # wrong-element capture). The Win32-level window-under-point
            # check (_point_is_target/top_window_at) that gates whether this
            # event gets emitted at all is a SEPARATE mechanism from this
            # UIA-level ElementFromPoint call, and the two can disagree.
            # Cross-check the element's own owning hwnd (walking ancestors,
            # not the click point) against target_hwnds/_popup_hwnds — drop
            # the selector like an unresolvable light-dismiss hit rather than
            # emit a selector for a control the recording was never tracking.
            if not light_dismiss and elem is not None:
                elem_hwnd = ins.resolve_root_hwnd(elem)
                # 2026-08-05 (TeamViewer 체크박스/라벨 실측): resolve_root_hwnd()는
                # elem 자신의 hwnd=0인 WebView2 콘텐츠에서 항상 0을 반환하므로,
                # 이 요소가 진짜 추적 중인 창에 속해도 매번 "추적 안 됨"으로
                # 떨어져 light_dismiss 복구(element_under_overlay, 아래)로
                # 넘어간다 — element_at()이 이미 성공적으로 찾아낸(스몰리스트,
                # 2026-08-05 수정 참고) 결과를 버리고 다른 방식으로 다시
                # 검색하는 셈인데, 이 재검색이 실패하는 경우가 있다(실측:
                # "이 장치에 Easy Access 권한 부여" 체크박스). element_at()이
                # 이 요소를 찾기 위해 실제로 검색한 창(trace의 root_hwnd)은
                # 이미 그 시점에 resolve_root_hwnd() 또는 GetForegroundWindow()
                # 폴백을 거쳐 검증된 값이므로, elem_hwnd가 0일 때 그 값을
                # 대신 쓴다 — 이 도구 자신의 Chrome 창이 오염원이었다면 그
                # root_hwnd도 마찬가지로 그 창을 가리켰을 것이므로(같은 신뢰
                # 경계), 자기 오염 방지 효과는 그대로 유지된다.
                if not elem_hwnd:
                    elem_hwnd = ins._last_trace.get("root_hwnd") or 0
                if (elem_hwnd not in self.target_hwnds
                        and elem_hwnd not in self._popup_hwnds):
                    # elem_hwnd == 0 means resolve_root_hwnd() gave up after
                    # 15 ancestor hops without finding a native window handle
                    # — routine for an element deep in a Chromium/React DOM
                    # tree, not a rare edge case. The ORIGINAL `elem_hwnd and
                    # ...` guard here short-circuited on 0 and skipped this
                    # whole tracked-window check — "couldn't determine the
                    # owner" was silently treated as "trust it". Measured
                    # 2026-08-04 (PuTTY): this tool's own control-panel tab
                    # got captured as PuTTY clicks — the "Captured Events (N)"
                    # counter label (ui/EventTable.jsx) and the React root div
                    # (id="root", ui/index.html) both got adopted verbatim.
                    #
                    # A first attempt fell back to top_window_at(x, y) for
                    # the CHECK only when elem_hwnd was 0 — but that only
                    # answers "what window is at this point right now", not
                    # "does elem actually belong to it". Re-verified
                    # 2026-08-04: a further recording still captured the
                    # SAME 'root'/'Captured Events' elements with no
                    # "not a tracked window" log line at all — the worker
                    # thread's delayed (0.2-0.5s) inspection let
                    # top_window_at() land back on the tracked app's window
                    # while `elem` was STILL the wrong (Chrome) element from
                    # the earlier ElementFromPoint() call, so the proxy check
                    # passed while the element it was standing in for did
                    # not. Checking the point and trusting `elem` are two
                    # different questions; a coordinate-based stand-in can't
                    # answer the second one.
                    #
                    # Distrust unconditionally instead (elem_hwnd == 0 is
                    # never "in" target_hwnds/popup_hwnds, so the branch
                    # above already routes it here) and let the EXISTING
                    # recovery below (element_under_overlay) re-derive the
                    # real element by walking foreground_top_window()'s own
                    # tree — anchored to a known window, not a raw
                    # coordinate hit test that can straddle two windows.
                    #
                    # _watch_windows() only registers new popup hwnds on its
                    # ~0.5s poll — a dialog can appear and get hit-tested here
                    # before that poll catches up (confirmed 2026-07-15: 7-Zip
                    # "확인" button on a freshly-opened overwrite dialog was
                    # correctly resolved but dropped as "untracked", losing a
                    # selector that then hard-failed replay). Mirror
                    # _foreground_is_target()'s PID self-heal (2026-07-13)
                    # instead of only trusting the watcher's snapshot — a real
                    # unrelated window (different PID, e.g. the Calculator
                    # cross-contamination bug fixed 2026-07-13) still gets
                    # rejected below. Guard on elem_hwnd itself first: pid 0
                    # never legitimately owns an app window, and adding hwnd
                    # 0 to target_hwnds would be a bug in its own right.
                    if elem_hwnd and self._owned_by_app(pid_of_hwnd(elem_hwnd)):
                        self.target_hwnds.add(elem_hwnd)
                        self._popup_hwnds.add(elem_hwnd)
                        log(f"[inspect] self-heal hwnd={elem_hwnd} "
                            f"(name={info.get('name')!r}) — accepted "
                            "(pre-empts 0.5s watcher poll; PID or same install dir)")
                        # 2026-08-12 (FileZilla STEP2/STEP3 불일치 실측): 이
                        # 값은 UIAInspector(ins)에서 초기화/기록되는데
                        # (286/1883행), 여기서 self(=Recorder)를 읽고 있었다
                        # — Recorder엔 이 속성이 아예 없어 self-heal될 때마다
                        # AttributeError가 났고, 그 예외가 (Fix A 덕에)
                        # 조용히 삼켜지면서 이 클릭의 ancestor+index 계산이
                        # 통째로 스킵돼 이름 기반 셀렉터로 강등되고 있었다.
                        if (ins._menu_snapshot_fail_ts is not None
                                and time.time() - ins._menu_snapshot_fail_ts
                                <= AMBIGUOUS_SELF_HEAL_WINDOW_S):
                            ambiguous_self_heal = True
                            log(f"[inspect] self-heal hwnd={elem_hwnd} landed "
                                f"{time.time() - ins._menu_snapshot_fail_ts:.2f}s "
                                "after a failed popup-menu snapshot — flagging as "
                                "ambiguous capture (menu-close/dialog-open race)")
                    else:
                        log(f"[inspect] element hwnd={elem_hwnd} not a tracked "
                            f"window (name={info.get('name')!r} "
                            f"rect={info.get('rect')!r}) — dropping selector")
                        light_dismiss = True
            # Adopted element's own bounding rect doesn't contain the click
            # point (confirmed 2026-07-13: PuTTY capture picked the
            # 'Selection' TreeItem with rect left=567 for a click at x=557 —
            # 10px into the tree's indent margin). A real physical click
            # there is a no-op in most native tree/list controls (nothing
            # under the cursor to hit), but replaying via UIA Invoke always
            # lands dead-center on whatever element was recorded, silently
            # producing a state change (selection swap) recording never had.
            # Treat exactly like an unresolvable light-dismiss hit: drop the
            # selector entirely rather than emit an anchor path that would
            # just re-target the same wrong node through a different XPath.
            if (not light_dismiss and elem is not None
                    and isinstance(info.get("rect"), tuple)):
                if not point_in_rect(info["rect"], x, y):
                    # 2026-08-05: 이 미스가 "정확히 경계 픽셀"이면 아래의 어떤
                    # 복구 경로도 구조적으로 성공할 수 없다 — 열린 드롭다운/메뉴
                    # 항목은 트리거에서 수십 픽셀 아래에 있지 경계 픽셀 위에
                    # 있지 않고, light-dismiss 스크림도 아니다. 그런데 그
                    # 헛수고가 실측 2.4초를 태워 워커를 영구히 뒤처지게 만들고,
                    # 그 결과 이후 모든 메뉴 스냅샷이 늦게 돌아 menuItemIndex가
                    # 세션 전체에서 유실됐다(클릭 하나 손실이 녹화 전체 손실로
                    # 증폭). 즉시 셀렉터를 버리고 반환해 그 증폭을 끊는다 —
                    # 버리는 판정 자체는 point_in_rect의 문서화된 안전 규칙
                    # 그대로다(존재하지 않았던 클릭을 재생하는 쪽이 더 나쁘다).
                    if is_exclusive_edge_miss(info["rect"], x, y):
                        log(f"[inspect] pt=({x},{y}) landed exactly on the "
                            f"right/bottom edge of rect={info['rect']} "
                            f"(name={info.get('name')!r}) — Win32/UIA treat that "
                            "edge as OUTSIDE the control, and a recorded click "
                            "that may not have hit anything must not be replayed "
                            "as a centre-click. Dropping this step; click a few "
                            "pixels further inside the control when recording.")
                        for k in ("name", "automationId", "className", "controlType"):
                            info[k] = ""
                        info["locatorStrategy"] = "coordinate"
                        info["locatorValue"] = ""
                        info["locatorFallback"] = "coordinate"
                        return info
                    # Before discarding: an OPEN combo dropdown always looks
                    # like this — the hit test returns the combo (rect = the
                    # collapsed box) while the click is on a list item below
                    # it. See UIAInspector.open_dropdown_item_at.
                    combo_hit = None
                    if ins._is_combo_like(info):
                        combo_hit = ins.open_dropdown_item_at(elem, x, y)
                    menu_hit = None
                    if combo_hit is None and ins._is_menu_like(info):
                        menu_hit = ins.open_menu_item_at(elem, x, y)
                    if combo_hit is not None:
                        inner, idx, total, item_name = combo_hit
                        info = ins.describe(inner, fg_hwnd_hint=fg_hwnd_hint, uia=ins._uia)
                        elem = inner
                        info["comboItemIndex"] = idx
                        info["comboItemCount"] = total
                        info["comboItemName"] = item_name
                        info["expandCollapse"] = True
                        log(f"[inspect] pt=({x},{y}) is item {idx + 1}/{total} of an "
                            f"open dropdown (name={item_name!r}) — recorded as "
                            f"'expand this combo, then pick item #{idx}' instead of "
                            "dropping the selector")
                    elif menu_hit is not None:
                        trigger, idx, total, item_name = menu_hit
                        info = ins.describe(trigger, fg_hwnd_hint=fg_hwnd_hint, uia=ins._uia)
                        elem = trigger
                        info["menuItemIndex"] = idx
                        info["menuItemCount"] = total
                        info["menuItemName"] = item_name
                        info["expandCollapse"] = True
                        log(f"[inspect] pt=({x},{y}) is item {idx + 1}/{total} of an "
                            f"open menu (name={item_name!r}) — recorded as "
                            f"'expand this menu, then pick item #{idx}' instead of "
                            "dropping the selector")
                    else:
                        log(f"[inspect] pt=({x},{y}) outside adopted rect={info['rect']} "
                            f"(id={info.get('automationId')!r} name={info.get('name')!r}) "
                            "— dropping selector")
                        light_dismiss = True
                else:
                    # The click landed ON the control. For a combo/menu that
                    # means it just OPENED the list, and this is the only
                    # moment the list can be measured — the click that picks
                    # an item closes it first. See snapshot_open_dropdown()/
                    # snapshot_open_menu().
                    ins.snapshot_open_dropdown(elem, info)
                    ins.snapshot_open_menu(elem, info, extra_pids=self._target_pids())
            # The adopted element was DEAD by the time describe() read it —
            # its BoundingRectangle either raised (describe stores the reason
            # as an "ERR:..." string) or came back all-zero. Measured
            # 2026-08-05 on two separate FileZilla recordings:
            #
            #   raw=[id='5101' name='취소' rect="ERR:COMError:(-2147220991,
            #        '이벤트에서 가입자를 불러낼 수 없습니다.')" hwnd=0]
            #   raw=[id='' name='닫기' rect=(1702,217,1746,254) hwnd=0]
            #        -> describe() re-read it as rect=(0,0,0,0)
            #
            # (-2147220991 == 0x80040201 EVENT_E_ALL_SUBSCRIBERS_FAILED — the
            # UIA provider went away.) Both were real buttons whose own click
            # destroyed them: 취소 closes the Site Manager dialog, 닫기 closes
            # a dialog. The worker thread inspects 0.2-0.5s later, by which
            # time the provider is gone.
            #
            # The bug is what happened NEXT. A dead element falls into the
            # light-dismiss recovery below, which re-hit-tests the foreground
            # window's tree and adopts whatever STATIC BACKDROP happens to sit
            # under the point — measured: FileZilla's log pane
            # ('RichEdit Control', a Document) and its status bar
            # ('연결되지 않았음.', a Text). Those are genuine, named, and
            # really do contain the point, so every downstream guard accepts
            # them; server.js's stripWindowFillingContainers can't help either
            # (Document/Text are deliberately excluded, and neither is close
            # to the 80% window-fill ratio). The result is a step that replays
            # cleanly and does absolutely nothing — the exact silent false
            # PASS §3 forbids — while the user's real 취소/닫기 click is lost.
            #
            # A dead element's identity cannot be recovered from a point,
            # because the point now belongs to something else. Drop the
            # selector so codegen emits an explicit FAIL step (§3), which is
            # what "we could not determine what was clicked" honestly means.
            # This is narrow by construction: every genuine recovery case has
            # a VALID rect and is untouched — the XAML Light Dismiss overlay
            # (2026-07-12 Notepad, rect = the whole window), the untracked
            # WebView2 subtree (2026-08-05 TeamViewer), and the wrong-window
            # contamination guard (2026-08-04 PuTTY/Chrome, rect=(219,367,
            # 294,405)) all read their rects fine and are rejected for
            # semantic reasons, not read failures.
            if light_dismiss and elem is not None:
                _r = info.get("rect")
                _dead = (not isinstance(_r, tuple)) or _r == (0, 0, 0, 0)
                if _dead:
                    # 2026-08-05 (TeamViewer "TeamViewer에 로그인" 실측): 위
                    # 단락의 "죽은 요소의 정체는 좌표로부터 복구할 수 없다"는
                    # 결론에는 예외가 있다 — **그 좌표를 다시 히트테스트할 때만**
                    # 참이다. element_at()의 최초 describe(trace["raw_info"])는
                    # 요소가 아직 살아 있던 시점의 스냅샷이고, 그게 남아 있으면
                    # 재-히트테스트 없이 정체를 확정할 수 있다. 실측 로그:
                    #   raw=[name='TeamViewer에 로그인' rect=(594,617,804,668)]
                    #   두 번째 describe -> name='' rect=(0,0,0,0)
                    # 두 호출 사이 몇 ms 만에 죽은 것이고, 클릭 지점 (725,644)는
                    # 그 최초 rect 안에 정확히 들어간다.
                    #
                    # dedupeDoubleClicks의 "가장 이른 관측만 pre-navigation이라
                    # 신뢰 가능"과 같은 원칙이다. 다만 아무 raw나 믿으면 안 되므로
                    # 네 조건을 모두 요구한다:
                    #  (1) element_at()이 raw를 그대로 반환했을 것(picked_by가
                    #      raw-*) — deepen/조상-등반으로 바뀐 결과의 조상 정보를
                    #      쓰면 컨테이너 오적용이 된다
                    #  (2) raw rect가 유효한 tuple이고 클릭 지점을 실제로 포함
                    #  (3) raw에 name 또는 automationId가 있을 것
                    #  (4) element_at()이 검색한 root_hwnd가 추적 중인 창일 것 —
                    #      이게 Chrome 'Stop' 류의 자기-오염을 막는다(그 케이스는
                    #      root_hwnd가 target에 없다)
                    _raw = (ins._last_trace or {}).get("raw_info") or {}
                    _picked = str((ins._last_trace or {}).get("picked_by") or "")
                    _rroot = (ins._last_trace or {}).get("root_hwnd") or 0
                    _rrect = _raw.get("rect")
                    if (_picked.startswith("raw-")
                            and isinstance(_rrect, tuple)
                            and point_in_rect(_rrect, x, y)
                            and (_raw.get("name") or _raw.get("automationId"))
                            and _rroot in self.target_hwnds):
                        log(f"[inspect] adopted element at ({x},{y}) is dead "
                            f"(rect={_r!r}) but element_at()'s FIRST read caught it "
                            f"alive: name={_raw.get('name')!r} "
                            f"id={_raw.get('automationId')!r} rect={_rrect} — the "
                            "click point falls inside that rect and the element "
                            "belongs to a tracked window, so the earliest "
                            "observation identifies it. Using that instead of "
                            "dropping the step.")
                        # describe()는 locatorFallback을 안 채운다 — 이 함수
                        # 말미가 채우는데 여기서 조기 반환하므로 직접 넣는다
                        # (정상 캡처와 같은 모양을 유지).
                        _raw["locatorFallback"] = (
                            "coordinate" if _raw.get("locatorStrategy") == "coordinate" else "")
                        # 2026-08-08 (FileZilla 사이트관리자 닫기 버튼 실측): _raw는
                        # element_at() 내부의 별도 이른 describe() 호출 결과라
                        # root_hwnd_hint/uia가 전달되지 않아 rootHwnd가 안 채워져
                        # 있었다 — _emit()의 same_owner 판정(elem.rootHwnd가
                        # target_hwnds 멤버인지)이 이 경로에서는 항상 실패하는
                        # 원인이었다. 위 조건에서 이미 _rroot in self.target_hwnds를
                        # 검증했으므로 그대로 채워 넣는다 — 재조회 불필요.
                        _raw["rootHwnd"] = _rroot
                        return _raw
                    log(f"[inspect] adopted element at ({x},{y}) is dead "
                        f"(rect={_r!r}) — its provider went away before it could "
                        "be read, so the point no longer identifies it. Skipping "
                        "light-dismiss recovery, which would adopt whatever "
                        "static backdrop sits underneath (§3: an honest FAIL "
                        "step beats a silent no-op)")
                    # Exactly the shape the existing "no resolvable element"
                    # drop below produces — codegen already turns that into an
                    # explicit FAIL step; do not invent a second variant.
                    for k in ("name", "automationId", "className", "controlType"):
                        info[k] = ""
                    info["locatorStrategy"] = "coordinate"
                    info["locatorValue"] = ""
                    # The existing drop path reaches the tail of this function
                    # and picks this up there; an early return has to set it
                    # itself or the event ships with a stale/absent fallback.
                    info["locatorFallback"] = "coordinate"
                    return info

            if light_dismiss:
                # The click raced a menu/flyout opening: by the time this hit
                # test ran, the XAML light-dismiss overlay (a full-window,
                # click-anywhere-to-close scrim) already covered the point,
                # so the selector describes the overlay, not what the user
                # actually clicked (confirmed 2026-07-08: clicking Notepad's
                # File menu button was captured as '~Light Dismiss'
                # spanning the whole window; reconfirmed 2026-07-12: rapid
                # 파일→편집→보기 menu-bar clicks lost 2 of 3 selectors and the
                # generated test failed on the explicit no-selector steps).
                # The real control is still in the window's ControlView tree
                # underneath the scrim — re-hit-test from the foreground
                # window subtree, skipping the overlay (2026-07-12 fix).
                #
                # element_under_overlay() walks foreground_top_window()'s OWN
                # tree via _deepen(), so whatever it finds is a genuine
                # descendant of THAT window by construction — but it never
                # checks that THAT window is one we're recording. Its
                # premise (docstring: "the real control is still in the
                # window we are recording") holds for the XAML light-dismiss
                # case this was built for (2026-07-12, a menu closing within
                # the SAME app) but not for the "wrong window entirely" case
                # the tracked-window check above now also routes here as of
                # today's fix. Measured 2026-08-04 (PuTTY): with that check
                # now catching more cases, several of them hit this path
                # while foreground_top_window() was this tool's OWN Chrome
                # tab (not PuTTY) — silently returning "Captured Events (N)"
                # / the React root div (id="root") with NO "not a tracked
                # window" log at all, because nothing here ever asked whether
                # the foreground window was PuTTY's. Ask first — searching an
                # untracked window's tree can only reproduce the same
                # wrong-window mistake through a second code path.
                fg = foreground_top_window()
                fg_is_target = fg and (fg in self.target_hwnds or fg in self._popup_hwnds)
                if fg and not fg_is_target and self._owned_by_app(pid_of_hwnd(fg)):
                    # Same self-heal as the direct-hit check above: a fresh
                    # popup of the SAME app can become foreground before the
                    # ~0.5s watcher poll registers it.
                    self.target_hwnds.add(fg)
                    self._popup_hwnds.add(fg)
                    log(f"[inspect] self-heal hwnd={fg} — accepted as light-dismiss "
                        "recovery root (pre-empts 0.5s watcher poll; PID or same install dir)")
                    fg_is_target = True
                under = ins.element_under_overlay(x, y) if fg_is_target else None
                if fg and not fg_is_target:
                    log(f"[inspect] foreground window hwnd={fg} is not tracked — "
                        "skipping light-dismiss recovery (would search the "
                        "wrong app's tree)")
                if under is not None:
                    resolved = ins.describe(under, fg_hwnd_hint=fg_hwnd_hint, uia=ins._uia)
                    # The re-hit-test must land on something that actually
                    # contains the point, or we would re-adopt the very kind
                    # of near-miss the guard above just rejected — only
                    # through a second code path (see point_in_rect).
                    if (isinstance(resolved.get("rect"), tuple)
                            and not point_in_rect(resolved["rect"], x, y)):
                        log(f"[inspect] under-overlay candidate "
                            f"{resolved.get('name')!r} rect={resolved.get('rect')!r} "
                            f"does not contain ({x},{y}) — not adopting")
                    elif resolved.get("automationId") or resolved.get("name"):
                        info = resolved
                        elem = under   # keep anchor/row logic consistent below
                        light_dismiss = False
                        log(f"[inspect] resolved under light-dismiss at ({x},{y}): "
                            f"id={info.get('automationId')!r} name={info.get('name')!r} "
                            f"type={info.get('controlType')!r}")
                        # A hwnd-less trigger (DropDown arrow, "더 보기" —
                        # any UIA-virtual sub-element always resolves
                        # elem_hwnd==0) reaches THIS recovery path instead of
                        # the point_in_rect/snapshot branch above, because
                        # elem_hwnd==0 sets light_dismiss=True before that
                        # branch ever runs — measured 2026-08-04 (HeidiSQL):
                        # the opening click on the network-type combo's
                        # DropDown arrow never called snapshot_open_dropdown()
                        # at all, so the very next click (an item) found no
                        # cache and no live list (already closed), and fell
                        # through to whatever control sat behind the list —
                        # a '자격 증명 프롬프트' CheckBox the user never
                        # touched. Recovering identity here is not enough;
                        # a combo/menu that "just opened" needs its snapshot
                        # taken NOW, at the only moment the list exists.
                        if (isinstance(info.get("rect"), tuple)
                                and point_in_rect(info["rect"], x, y)):
                            ins.snapshot_open_dropdown(elem, info)
                            ins.snapshot_open_menu(elem, info, extra_pids=self._target_pids())
                if light_dismiss:
                    # Still nothing usable — coordinate replay is forbidden
                    # (2026-07-10), so codegen will surface this event as an
                    # explicit failing step.
                    log(f"[inspect] no resolvable element at ({x},{y}) — dropping selector")
                    for k in ("name", "automationId", "className", "controlType"):
                        info[k] = ""
                    info["locatorStrategy"] = "coordinate"
                    info["locatorValue"] = ""
            # Fallback resolved to the whole Tree control (its center-click
            # semantics at replay depend on whatever's currently painted
            # there) — prefer the specific TreeItem row the click's y lines
            # up with, e.g. a +/- toggle glyph click (2026-07-13).
            treeitem_glyph_fallback = False
            if (not light_dismiss and elem is not None
                    and info.get("controlType") == "Tree"):
                row = ins.tree_item_at_row(elem, y)
                if row is not None:
                    row_info = ins.describe(row, fg_hwnd_hint=fg_hwnd_hint, uia=ins._uia)
                    if row_info.get("name") or row_info.get("automationId"):
                        info = row_info
                        elem = row
                        treeitem_glyph_fallback = True
                        log(f"[inspect] Tree-center fallback narrowed to row "
                            f"TreeItem name={info.get('name')!r}")
            # 이름/ID 없는 ListItem이 사실은 "열린 콤보 드롭다운의 항목"인 경우
            # (2026-07-31, HeidiSQL ComboBoxEx): anchor XPath로 넘기기 전에
            # 먼저 잡는다. anchor는 트리 구조 기반이라 매 실행 위치가 같아야
            # 하는데, 드롭다운 목록은 열려 있을 때만 존재하므로 anchor의 전제가
            # 성립하지 않는다. 대신 "이 콤보를 펼친 뒤 N번째 항목"으로 기록한다.
            if (not light_dismiss and elem is not None
                    and not info.get("automationId") and not info.get("name")):
                self_hit = ins.combo_item_self(elem, info)
                if self_hit is not None:
                    inner, idx, total = self_hit
                    info = ins.describe(inner, fg_hwnd_hint=fg_hwnd_hint, uia=ins._uia)
                    elem = inner
                    info["comboItemIndex"] = idx
                    info["comboItemCount"] = total
                    info["comboItemName"] = ""
                    info["expandCollapse"] = True
                    log(f"[inspect] nameless dropdown item at ({x},{y}) is #{idx} of "
                        f"{total} — recorded as 'expand this combo, then pick "
                        f"item #{idx}'")
            # 이름/ID 없는 MenuItem이 사실은 "열린 팝업 메뉴의 항목"인 경우
            # (2026-08-04, HeidiSQL "더 보기" — 아이콘 전용, 콤보와 같은 문제).
            if (not light_dismiss and elem is not None
                    and not info.get("automationId") and not info.get("name")):
                menu_self_hit = ins.menu_item_self(elem, info)
                if menu_self_hit is not None:
                    trigger, idx, total = menu_self_hit
                    info = ins.describe(trigger, fg_hwnd_hint=fg_hwnd_hint, uia=ins._uia)
                    elem = trigger
                    info["menuItemIndex"] = idx
                    info["menuItemCount"] = total
                    info["menuItemName"] = ""
                    info["expandCollapse"] = True
                    log(f"[inspect] nameless menu item at ({x},{y}) is #{idx} of "
                        f"{total} — recorded as 'expand this menu, then pick "
                        f"item #{idx}'")
            # 유니크 id/name이 없는 요소 → anchor 기반 relative XPath 캡처
            # (2026-07-10: 좌표 재생 금지 — anchor XPath가 유일한 재생 수단).
            # light-dismiss 오버레이는 전체 창을 덮는 요소라 anchor가 무의미.
            if (not light_dismiss and elem is not None
                    and not info.get("automationId") and not info.get("name")):
                a = ins.anchor_path(elem)
                if a:
                    info["anchorId"], info["anchorPath"] = a
                    info["locatorStrategy"] = "anchorXPath"
                    info["locatorValue"] = f'//*[@AutomationId="{a[0]}"]{a[1]}'
                    info["xpath"] = info["locatorValue"]
                    log(f"[inspect] anchor XPath for id/name-less element: {info['xpath']}")
            # 부모(이름 있는 조상) + 동일 ControlType 형제 순번 셀렉터
            # (2026-08-08, UIAInspector._ancestor_sibling_selector) —
            # anchor_path()가 커버 못 하는 케이스(조상이 AutomationId 없이
            # Name만 있는 경우, FileZilla 툴바 Pane처럼) 대비. anchor_path와
            # 병행 계산 — 둘 다 채워둘 수 있음, 서로 배타적이지 않음.
            # 2026-08-08 (FileZilla 로컬 파일목록 — blob_storage/Cache/…):
            # automationId/className 없이 Name에 파일/폴더명을 그대로
            # 흘려보내는 ListItem/TreeItem은 Name이 있어도 그 값이 녹화 시점
            # 디스크 상태에 종속적이라 재생 시점엔 존재하지 않을 수 있다
            # (TComboBoxEx의 "Name이 현재 선택값"과 같은 계열의 volatile
            # Name 문제 — CLAUDE.md §5). automationId도 className도 없는
            # ListItem/TreeItem은 name이 있어도 구조적(순번) 셀렉터를
            # 우선한다. _find_sibling_by_controltype()은 target_name을
            # 이미 타이브레이커로 지원하므로 별도 수정 불필요.
            info_ct = info.get("controlType")
            volatile_named_item = (
                not info.get("automationId")
                and not info.get("className")
                and info_ct in ("ListItem", "TreeItem")
            )
            if (not light_dismiss and elem is not None
                    and ((not info.get("automationId") and not info.get("name"))
                         or volatile_named_item)):
                anc = ins._ancestor_sibling_selector(
                    elem,
                    cached_rect=info.get("rect"),
                    cached_ct=info.get("controlTypeId"),
                    cached_name=info.get("name"),
                )
                if anc is not None:
                    ancestor_elem, sib_idx, sib_count = anc
                    anc_info = ins.describe(ancestor_elem, fg_hwnd_hint=fg_hwnd_hint, uia=ins._uia)
                    info["ancestorAutomationId"] = anc_info.get("automationId", "")
                    info["ancestorName"] = anc_info.get("name", "")
                    info["ancestorClassName"] = anc_info.get("className", "")
                    info["ancestorHwnd"] = anc_info.get("hwnd", 0)
                    info["ancestorSiblingIndex"] = sib_idx
                    info["ancestorSiblingCount"] = sib_count
                    try:
                        info["ancestorItemControlTypeId"] = elem.CurrentControlType
                    except Exception:
                        info["ancestorItemControlTypeId"] = None
                    log(f"[inspect] ancestor+index selector for nameless element: "
                        f"ancestor(id={anc_info.get('automationId')!r} "
                        f"name={anc_info.get('name')!r}) sibling #{sib_idx}/{sib_count} "
                        f"(controlType={info.get('controlType')!r})")
                    if ambiguous_self_heal:
                        # This click itself was accepted via a self-heal that
                        # landed suspiciously soon after a failed popup-menu
                        # snapshot — the ancestor+index selector we just built
                        # may point at the wrong window's element entirely
                        # (§CLAUDE.md hard rule: no silent coordinate/guess
                        # fallback). Keep the fields above for diagnostics but
                        # let codegen turn this into an explicit FAIL step
                        # instead of a fragile osAncestorInvoke() call.
                        info["ambiguousCapture"] = True
                        log("[inspect] ancestor+index selector marked "
                            "ambiguousCapture — codegen will emit an explicit "
                            "FAIL step instead of replaying it")
            # ExpandCollapsePattern 태깅 — 2026-07-13 진단(poc/diag_expandcollapse.py)으로
            # ComboBox/메뉴바 MenuItem은 일반 클릭만으로 "펼치기"가 재현 안
            # 됨을 실증했지만, **ExpandCollapsePattern "지원 여부"만으로
            # 판단하면 안 된다** — 재녹화 실측(2026-07-13, FileZilla 폴더
            # 트리+주소창 breadcrumb)에서 TreeItem/Edit 등 거의 모든 컨트롤이
            # 이 패턴을 구현하고 있어 정상적으로 잘 동작하던 클릭(폴더 탐색,
            # breadcrumb 이동)까지 전부 "펼치기 전용" 호출로 가로채 실제 클릭이
            # 통째로 사라지는 회귀를 유발했다(전부 rect 안쪽의 정상 클릭이었음
            # — pt-밖-rect 폴백이 아님). ComboBox/MenuItem은 그 자체가 펼치기
            # 외의 다른 상호작용이 없는 컨트롤이라 항상 태깅하지만, TreeItem은
            # 위 glyph 폴백(pt가 항목 자체 rect 밖이라 행 단위로 재해석된 경우)
            # 에서만 태깅 — 그 외 컨트롤 타입은 지원 여부와 무관하게 절대 태깅
            # 안 함(일반 클릭이 이미 정상 동작).
            EXPAND_COLLAPSE_ALWAYS = ("ComboBox", "MenuItem")
            ct = info.get("controlType")
            wants_expand_collapse = (
                ct in EXPAND_COLLAPSE_ALWAYS
                or (ct == "TreeItem" and treeitem_glyph_fallback)
            )
            if (not light_dismiss and elem is not None and wants_expand_collapse
                    and ins.has_expand_collapse(elem)):
                info["expandCollapse"] = True
                log(f"[inspect] ExpandCollapsePattern available on "
                    f"{info.get('controlType')!r} name={info.get('name')!r}")
            # locatorFallback mirrors locatorStrategy for backwards compat
            if info.get("locatorStrategy") == "coordinate":
                info["locatorFallback"] = "coordinate"
            else:
                info["locatorFallback"] = ""
            return info
        except Exception:
            # 2026-08-11 (FileZilla 체크박스 / Notepad 탭닫기→저장하지 않음
            # 실측): 이 블록은 예외를 완전히 삼키고 항상 빈 dict를
            # 반환해왔다 — self-heal 로그(위 3888행 부근, info.get('name')
            # 사용)가 이미 실측한 정상 name/rect를 찍은 바로 다음, 어딘가의
            # 라이브 재조회가 던진 예외 때문에 그 값이 통째로 버려지고
            # 있었다(로그에 예외 자체가 안 찍혀 원인 호출부를 특정 못 했던
            # 이유이기도 하다). light-dismiss 복구(위 4055~4084행 부근,
            # "FIRST read caught it alive")와 같은 원칙 — 이미 확보한 관측을
            # 버리지 않는다. 단, name/automationId만 보고 판단하지 않는다:
            # rect가 아직 안 채워졌거나 형태가 깨진 반쪽짜리 info를 내보내면
            # rect를 기대하는 다른 코드(_ancestor_sibling_selector의
            # cached_rect, _flush_type_buffer의 좌표 계산 등)가 새로운
            # 방식으로 죽을 수 있으므로, rect가 정상 4-tuple/리스트일 때만
            # 신뢰하고 그 외엔 기존처럼 좌표 폴백 빈 dict로 떨어진다.
            rect = info.get("rect") if info else None
            has_valid_rect = isinstance(rect, (tuple, list)) and len(rect) == 4
            if info and (info.get("name") or info.get("automationId")) and has_valid_rect:
                log(f"[inspect] exception after info was already resolved "
                    f"(name={info.get('name')!r} automationId="
                    f"{info.get('automationId')!r}) — keeping the earlier "
                    "read instead of blanking it:")
                traceback.print_exc()
                info.setdefault("locatorFallback",
                                 "" if info.get("locatorStrategy") != "coordinate" else "coordinate")
                return info
            return {"automationId": "", "className": "", "name": "",
                    "controlType": "", "windowTitle": "", "xpath": "",
                    "hwnd": 0, "rootHwnd": 0,
                    "locatorStrategy": "coordinate", "locatorValue": "",
                    "locatorFallback": "coordinate"}

    # ---------------- pending flushes ----------------
    def _flush_stale(self, ins):
        now = time.time()
        if self._pending_scroll and now - self._pending_scroll["ts"] > SCROLL_FLUSH_IDLE:
            self._flush_pending_scroll()
        if self._pending_activation and now >= self._pending_activation["due"]:
            self._flush_pending_activation(ins, verify=True)

    def _emit_click_from_press(self, press, ins=None):
        """Emit click (+ doubleClick if paired) for a completed left press.
        Shared by the release handler and the stale-press flush so both
        paths reproduce identical click/double-click semantics. `ins` is
        only needed to compute an activation-check snapshot (2026-08-10) —
        callers with no live UIAInspector at hand may omit it, in which case
        candidate clicks just skip the snapshot (their later verification
        will find every snapshot field None and quietly not tag the
        flag — never worse than before this feature existed)."""
        cx, cy, ts, elem = press["x"], press["y"], press["ts"], press["elem"]
        com_elem = press.get("com_elem")
        self._last_click_xy = (cx, cy)

        ll = self._last_left_click
        if (ll and ts - ll["ts"] <= DOUBLE_CLICK_INTERVAL
                and abs(cx - ll["x"]) <= DOUBLE_CLICK_RADIUS
                and abs(cy - ll["y"]) <= DOUBLE_CLICK_RADIUS):
            # 2026-08-09: elem은 이미 press 시점에 첫 서브클릭의 결과와
            # 동일하게 강제돼 있다(press 핸들러의 더블클릭 재사용 로직 참고) —
            # 여기서 다시 비교할 게 없다.
            # 2026-08-10: 첫 서브클릭의 click 이벤트가 활성화 감지로 보류돼
            # 있었을 수 있다(방어적 — 이 페어링 조건상 흔치 않음) — 이제
            # doubleClick으로 확정됐으니 검증 없이 그대로 흘려보낸다. 물리
            # 더블클릭은 실제 동작을 일으키므로 플래그가 필요 없다.
            self._flush_pending_activation(verify=False)
            self._emit("click", elem, x=cx, y=cy, ts=ts)
            self._emit("doubleClick", elem, x=cx, y=cy, ts=ts)
            self._last_left_click = None  # consume; avoid chaining triples
            return
        # 페어링되지 않은(적어도 지금까지는) 단독 클릭. 이름 없는(또는
        # automationId/className 둘 다 없는) ListItem/TreeItem이면 즉시
        # 방출하지 않고 실제로 뷰가 바뀌었는지(선택이 아니라 네비게이션/
        # 펼치기) 확인할 시간을 준다 — 2026-08-10, FileZilla '..' 실측:
        # 재생 시점엔 "선택 의도였는지 이동 의도였는지" 추측만 가능하지만,
        # 캡처 시점엔 실제로 관측할 수 있다.
        if self._is_activation_check_candidate(elem):
            # 2026-08-10 (PuTTY 도움말 트리 실측 — 서로 다른 항목을 0.4초
            # 안에 연속 단클릭하면 앞 클릭이 통째로 사라짐): 이전 클릭이
            # 활성화-확인 대기 중(아직 ACTIVATION_CHECK_DELAY가 안 지남)일
            # 때 여기 다시 들어오면, 그 대기값을 그냥 덮어써서 이벤트 자체가
            # 유실됐다. _pending_press가 새 press를 받기 전에 이전 값을
            # 먼저 flush하는 것(3145행)과 같은 원칙 — 새 후보를 보류시키기
            # 전에 기존 보류값부터 내보낸다. 더블클릭 페어링 분기(위,
            # 4343행)는 이미 flush하므로 이 변경의 영향을 안 받는다.
            if self._pending_activation is not None:
                self._flush_pending_activation(verify=False)
            self._pending_activation = {
                "elem": elem, "com_elem": com_elem, "x": cx, "y": cy, "ts": ts,
                "due": time.time() + ACTIVATION_CHECK_DELAY,
                "snapshot": self._activation_snapshot(ins, com_elem),
            }
        else:
            # Every left click is recorded individually (preserves repeated
            # presses like "9999" -> num9Button x4). A genuine fast
            # double-click is recognised IN ADDITION, never by
            # merging/dropping the clicks.
            self._emit("click", elem, x=cx, y=cy, ts=ts)
        self._last_left_click = {"x": cx, "y": cy, "ts": ts, "elem": elem, "com_elem": com_elem}

    @staticmethod
    def _is_activation_check_candidate(elem):
        """이름 없는(또는 automationId/className 둘 다 없는) ListItem/TreeItem
        — _ancestor_sibling_selector가 구조적 셀렉터를 만드는 것과 같은
        집합(2번 이슈의 volatile_named_item과 동일 조건, 2026-08-10). 단일
        클릭이 실제로 뷰를 바꿨는지 검증할 가치가 있는 대상만 골라낸다 —
        이름/ID가 있는 평범한 버튼·행은 대상에서 제외해 불필요한 지연을
        피한다."""
        if elem.get("controlType") not in ("ListItem", "TreeItem"):
            return False
        return not elem.get("automationId") and not elem.get("className")

    def _activation_snapshot(self, ins, com_elem):
        """클릭 시점 상태 스냅샷 — ExpandCollapseState와 직계 자식 개수.
        읽기 실패한 필드는 None으로 남고, 나중에 그 필드만으로는 '변화
        없음'을 증명하지 못할 뿐(다른 필드나 예외 감지가 대신 잡아준다)."""
        snap = {"ecs": None, "children": None}
        if com_elem is None:
            return snap
        try:
            pattern = com_elem.GetCurrentPattern(UIA_EXPAND_COLLAPSE_PATTERN_ID)
            if pattern:
                snap["ecs"] = pattern.QueryInterface(
                    ins._mod.IUIAutomationExpandCollapsePattern
                ).CurrentExpandCollapseState
        except Exception:
            pass
        try:
            arr = com_elem.FindAll(2, ins._uia.CreateTrueCondition())  # TreeScope_Children
            snap["children"] = arr.Length if arr else 0
        except Exception:
            pass
        return snap

    def _activation_changed(self, ins, com_elem, snapshot):
        """하이브리드 검증(2026-08-10, FileZilla '..' 실측 + 사용자 리뷰
        피드백) — 순서대로 확인:
        ① 요소 자체가 파괴됐는가(리스트 이동처럼 행 전체가 사라지는 경우 —
           COM 예외 자체가 100% 확실한 신호).
        ② 살아있다면 ExpandCollapseState가 바뀌었는가(트리 노드를 펼쳐도
           노드 자신은 안 죽고 자식만 새로 생기는 경우 — ①로는 못 잡음).
        ③ 그것도 아니면 직계 자식 개수가 바뀌었는가(②의 패턴이 없는
           컨트롤 대비 보조 신호).
        단순 형제 개수 비교만으로는 우연히 같은 개수인 다른 폴더로 이동한
        경우를 놓친다 — ①이 그 케이스(행 자체가 파괴됨)를 먼저 잡는다."""
        if com_elem is None:
            return False
        try:
            _ = com_elem.CurrentBoundingRectangle  # ① 생존 검사
        except Exception:
            return True
        try:
            pattern = com_elem.GetCurrentPattern(UIA_EXPAND_COLLAPSE_PATTERN_ID)
            if pattern and snapshot.get("ecs") is not None:
                now_ecs = pattern.QueryInterface(
                    ins._mod.IUIAutomationExpandCollapsePattern
                ).CurrentExpandCollapseState
                if now_ecs != snapshot["ecs"]:
                    return True
        except Exception:
            pass
        try:
            if snapshot.get("children") is not None:
                arr = com_elem.FindAll(2, ins._uia.CreateTrueCondition())
                now_children = arr.Length if arr else 0
                if now_children != snapshot["children"]:
                    return True
        except Exception:
            pass
        return False

    def _flush_pending_activation(self, ins=None, verify=False):
        pa, self._pending_activation = self._pending_activation, None
        if not pa:
            return
        flag = False
        if verify and ins is not None:
            flag = self._activation_changed(ins, pa["com_elem"], pa["snapshot"])
            if flag:
                log(f"[activation] single click on {pa['elem'].get('controlType')!r} "
                    f"name={pa['elem'].get('name')!r} actually changed the view — "
                    f"tagging activatesOnSingleClick")
        self._emit("click", pa["elem"], x=pa["x"], y=pa["y"], ts=pa["ts"],
                    extra={"activatesOnSingleClick": True} if flag else None)

    def _flush_pending_click(self, ins=None):
        # A pending left press with no release yet (e.g. focus moved before
        # button-up, or the release was lost) must not be silently dropped —
        # emit it as a plain click, same as a completed press+release would.
        if self._pending_press is not None:
            press, self._pending_press = self._pending_press, None
            self._emit_click_from_press(press, ins)
        # 2026-08-10: 보류 중이던 활성화-감지 클릭이 있으면(다른 이벤트가
        # 끼어들어 확정 전에 인터럽트된 경우) 검증 없이 그대로 방출한다 —
        # 플래그를 달려면 실제로 0.4초를 기다려야 하는데, 지금은 다른
        # 이벤트를 처리해야 해서 더 기다릴 수 없다. 페어링돼 doubleClick이
        # 되는 경우는 이미 위에서 처리되므로 여기 도달하지 않는다.
        self._flush_pending_activation(verify=False)
        # Ends the open double-click window so an intervening event can't
        # pair across it.
        self._last_left_click = None

    def _flush_pending_scroll(self):
        ps, self._pending_scroll = self._pending_scroll, None
        if ps:
            extra = {"scrollTarget": ps["target"]} if ps.get("target") else None
            self._emit("scroll", ps["elem"], x=ps["x"], y=ps["y"],
                       value=str(ps["amount"]), delta=ps["amount"], ts=ps["ts"],
                       extra=extra)

    def _flush_type_buffer(self):
        text, self._type_buffer = self._type_buffer, ""
        elem, self._type_elem = self._type_elem, None
        if text:
            cx = cy = None
            rect = (elem or {}).get("rect")
            # rect = (left, top, right, bottom) — filled in by describe()
            if isinstance(rect, (tuple, list)) and len(rect) == 4 and all(isinstance(v, int) for v in rect):
                cx = int((rect[0] + rect[2]) / 2)
                cy = int((rect[1] + rect[3]) / 2)
            else:
                log(f"[type-coord] focused rect unusable: {rect!r} — inheriting last click")
                if self._last_click_xy:
                    cx, cy = self._last_click_xy
            self._emit("type", elem or {}, x=cx, y=cy, value=text)

    def _emit_pointer_event(self, action, x, y, ins, ts=None):
        self._emit(action, self._inspect(ins, x, y), x=x, y=y, ts=ts)

    # ---------------- emission ----------------
    def _get_win_rect(self, hwnd):
        """Return (left, top, width, height) for hwnd, or None on failure."""
        try:
            if hwnd:
                left, top, right, bottom = win32gui.GetWindowRect(hwnd)
                return left, top, right - left, bottom - top
        except Exception:
            pass
        return None

    def _is_electron(self, hwnd):
        """True if the top-level window is an Electron (Chromium) app."""
        try:
            if hwnd:
                cls = win32gui.GetClassName(hwnd)
                return 'Chrome_WidgetWin' in cls
        except Exception:
            pass
        return False

    def _pick_frame_hwnd(self):
        """Pick a target hwnd for rect/geometry purposes, preferring one that
        isn't a UWP CoreWindow. Probed and confirmed (2026-07-06 session): a
        CoreWindow's GetWindowRect is a ghost rect pinned at the screen
        origin, not where the app is actually drawn/clicked — the
        ApplicationFrameWindow (its EnumChildWindows parent) has the real
        rect. target_hwnds is a set, so picking arbitrarily can silently
        select the CoreWindow and produce a bogus initialWindow/relX/relY."""
        for hwnd in self.target_hwnds:
            try:
                if win32gui.GetClassName(hwnd) != 'Windows.UI.Core.CoreWindow':
                    return hwnd
            except Exception:
                continue
        # Only CoreWindows tracked so far (frame not yet lazily adopted — this
        # runs right after discovery, before any click/keystroke can trigger
        # that). Search all currently visible top-level windows for the frame
        # that owns one of them, so session_meta doesn't lock onto the ghost
        # rect (confirmed 2026-07-08: Calculator's initialWindow came back
        # (0,26,322,500) — the CoreWindow's own rect — instead of the real
        # on-screen position).
        candidates = visible_toplevel_windows()
        for core in self.target_hwnds:
            frame = frame_owning_corewindow(core, candidates)
            if frame:
                return frame
        return next(iter(self.target_hwnds), 0)

    def _window_contains_child(self, parent_hwnd, target_child):
        """True if `target_child` is an EnumChildWindows descendant of
        `parent_hwnd`. UWP CoreWindow's parent/owner/GA_ROOT are all itself
        (probed 2026-07-06) — no upward link exists — so the frame that
        actually hosts a given CoreWindow can only be found by checking
        this direction, from candidate frame down to the known CoreWindow."""
        found = [False]

        def _enum(child, _):
            if child == target_child:
                found[0] = True
                return False
            return True

        try:
            win32gui.EnumChildWindows(parent_hwnd, _enum, None)
        except Exception:
            pass
        return found[0]

    def _remember_title(self, hwnd, title):
        """First title ever seen for `hwnd` wins — see self._first_titles."""
        if hwnd and title and hwnd not in self._first_titles:
            self._first_titles[hwnd] = title

    def _emit_session_meta(self):
        """Emit a session_meta event with initial window geometry."""
        hwnd = self._pick_frame_hwnd()
        rect = self._get_win_rect(hwnd)
        meta = {
            "action": "session_meta",
            "app": self.session.get("appName", ""),
            "platform": self.session.get("platform", "Windows"),
            "timestamp": time.time(),
        }
        if hwnd:
            meta["isElectron"] = self._is_electron(hwnd)
        if rect is not None:
            win_left, win_top, win_w, win_h = rect
            meta["initialWindow"] = {
                "left": win_left, "top": win_top,
                "width": win_w, "height": win_h,
            }
        # 창 발견 시점의 제목 — 사용자 조작으로 이름이 바뀌기 전의 "안정된"
        # 제목이다. HeidiSQL 세션 관리자는 '신규'를 누르는 순간 제목 뒤에
        # ": Unnamed-N"(실행마다 새로 매겨지는 일련번호)이 붙는데, 그 클릭이
        # 첫 이벤트면 녹화된 모든 이벤트가 바뀐 제목만 갖게 되어 codegen이
        # 되돌릴 근거를 잃는다. 실측 2026-08-03: 그렇게 만들어진 테스트는
        # 재생 때 launchApp이 창을 영영 못 찾아(matched=[]) 스텝이 단 하나도
        # 실행되지 않았다. server.js canonicalizeWindowTitles()가 이 값을
        # 병합 대상으로만 쓴다(별도 창 세그먼트를 만들지는 않는다).
        titles = []
        for h in sorted(self.target_hwnds):
            try:
                t = win32gui.GetWindowText(h)
            except Exception:
                continue
            self._remember_title(h, t)
            if t and t not in titles:
                titles.append(t)
        if titles:
            meta["discoveredTitles"] = titles
        try:
            requests.post(EXPRESS_EVENTS_URL, json=meta, timeout=3)
            log(f"[meta] session_meta emitted window={meta.get('initialWindow')}")
        except Exception as e:
            log(f"WARN: could not POST session_meta: {e}")

    def _emit(self, action, elem, x=None, y=None, value=None, delta=None, ts=None, end=None, extra=None):
        elem = elem or {}
        # Drop events captured before _discover_target_windows() resolved
        # target_hwnds. Mouse/keyboard hooks go live at recording=True, before
        # discovery finishes (up to DISCOVER_TIMEOUT later) — a click in that
        # gap (e.g. the launched app hasn't rendered its window yet) is only
        # ever *processed* after discovery completes (single worker thread),
        # so target_hwnds at processing time looks "resolved" even though it
        # was meaningless at the moment this event was actually captured.
        # Comparing the event's own capture timestamp (not "am I being
        # processed after discovery" — that's always true) is what actually
        # detects the gap.
        if action != "type" and ts is not None and ts < self._discovery_done_ts:
            log(f"[skip] {action} pre-discovery ts={ts:.3f} "
                f"discovery_done={self._discovery_done_ts:.3f}")
            return
        # Application filtering by top-level window handle.
        # Pointer events carry (x, y) — filter by the window under the point.
        # `type` events have no point; they were already gated on the
        # foreground window being the target at capture time.
        if action != "type" and x is not None:
            top = top_window_at(x, y)
            contradiction = False

            # UWP lazy frame adoption: the ApplicationFrameWindow that input
            # actually routes to is owned by ApplicationFrameHost.exe (a
            # different process), so path/pid matching in
            # _discover_target_windows can't identify it, and the only real
            # link to a target CoreWindow — EnumChildWindows(frame) —
            # sometimes isn't established yet at discovery time (probed
            # 2026-07-06). Re-check right now, at the moment a real click
            # tells us which window `top` is; cache the result once found.
            if top and top not in self.target_hwnds and top not in self._popup_hwnds:
                for core in list(self.target_hwnds):
                    if self._window_contains_child(top, core):
                        # target_hwnds alone is enough to flip
                        # is_known_other_window to False below — NOT
                        # _popup_hwnds, which also drives is_popup
                        # annotation (line ~1079) and would mislabel the
                        # main app frame as a popup dialog in codegen.
                        self.target_hwnds.add(top)
                        log(f"[target] lazy frame {top} hosts CoreWindow {core} — added")
                        break

            # The pointer is over a window we can positively identify as NOT
            # the target and NOT a popup of it (e.g. desktop "Program Manager",
            # taskbar tray). Skip immediately — do not let the foreground
            # fallback below wave this through just because the target
            # happens to still be the foreground window.
            # top==0 (UWP CoreWindow, GA_ROOT mismatch) stays unknown here,
            # so it still falls through to the foreground fallback.
            is_known_other_window = (
                top != 0 and top not in self.target_hwnds and top not in self._popup_hwnds
            )
            if is_known_other_window:
                # Self-heal by PID/install-dir before concluding this is a
                # truly unrelated window (2026-07-21, 7-Zip Benchmark repro):
                # this gate used to reject unconditionally, even for a
                # same-process (or same-install-dir sibling process, e.g.
                # 7zG.exe) window that _watch_windows()'s ~0.5s poll simply
                # hadn't caught up to yet — confirmed the very first click
                # inside a freshly-opened dialog can land ~240ms after it
                # appears, well before even one poll cycle. _inspect() already
                # had its own self-heal for this exact race, but it runs on a
                # UIA element's owning hwnd, which can differ from top_window_at
                # here — both must agree, or the click still gets dropped here
                # even after _inspect() successfully resolved a selector for it.
                if self._owned_by_app(pid_of_hwnd(top)):
                    self.target_hwnds.add(top)
                    self._popup_hwnds.add(top)
                    log(f"[target] self-heal (pid/install-dir) hwnd={top} "
                        f"title='{win32gui.GetWindowText(top)}' — accepted "
                        "before known-other-window gate")
                else:
                    # 2026-07-24 (PuTTY): the point can be geometrically INSIDE
                    # a window we are recording while top_window_at() names a
                    # foreign one. Dropping such an event silently deletes a
                    # real user action from the capture — the generated test
                    # then looks complete but skips, say, the click that
                    # switched dialog panels, and every later step fails for
                    # reasons nothing in the output explains. Keep the event.
                    # Default to blanking the selector (the element we
                    # hit-tested COULD belong to the OTHER window, so
                    # replaying it might click a stranger's UI) — UNLESS we
                    # already have independent, structural confirmation of
                    # which window the element really belongs to (see
                    # same_owner below), in which case blanking would throw
                    # away a selector we already know is correct.
                    #
                    # 2026-08-08 (2차 수정, FileZilla 닫기 버튼 실측): 처음엔
                    # same_owner를 tracked_window_containing(좌표 기반, margin
                    # 튜닝 필요)의 결과와 elem.rootHwnd를 비교해서 판단했는데,
                    # 실측: 좌표가 창 경계 한참 안쪽(마진도 필요 없는 지점)인데도
                    # tracked_window_containing이 0을 반환하는, 원인 미상의
                    # 사례가 나왔다(진단 로그 추가함, tracked_window_containing
                    # 참고). elem.rootHwnd 자체가 이미 target_hwnds의 멤버인지를
                    # **좌표 계산 없이 직접** 확인하는 쪽이 더 직접적이고
                    # 견고하다 — UIA가 구조적으로 확인한 소유 창이 우리가 이미
                    # 추적 중인 창이라면, 좌표가 그 창의 rect 안에 있는지는
                    # 재확인할 필요가 없다(이미 다른 방식으로 확인된 사실이다).
                    root_owner = elem.get("rootHwnd")
                    same_owner = bool(root_owner) and root_owner in self.target_hwnds
                    if same_owner:
                        inside = root_owner
                    else:
                        inside = tracked_window_containing(x, y, self.target_hwnds)
                    if inside:
                        try:
                            rect = win32gui.GetWindowRect(inside)
                        except Exception:
                            rect = None
                        if same_owner:
                            log(f"[contradiction-confirmed] {action} at ({x},{y}): "
                                f"element's own resolved window (rootHwnd="
                                f"{root_owner}) is already a tracked window — "
                                "keeping the already-verified selector instead "
                                "of blanking it (top_window_at disagreed, "
                                f"likely window overlap: top={top} "
                                f"title='{win32gui.GetWindowText(top)}')")
                        else:
                            log(f"[skip-contradiction] {action} at ({x},{y}) is INSIDE "
                                f"tracked window hwnd={inside} rect={rect}, but "
                                f"top_window_at returned {top} "
                                f"title='{win32gui.GetWindowText(top)}' "
                                f"(fg={foreground_top_window()}, elem_rect={elem.get('rect')}) "
                                "— emitting a no-selector FAIL step instead of "
                                "dropping the event")
                            elem = dict(elem)
                            elem["automationId"] = ""
                            elem["name"] = ""
                            elem["className"] = ""
                        contradiction = True
                    else:
                        log(f"[skip] {action} known-other-window top={top} "
                            f"title='{win32gui.GetWindowText(top)}' x={x} y={y}")
                        if not self._probed_skip:
                            self._probed_skip = True
                            probe_window("clickwin", top)
                        return
            # Accept if the point is over a target window OR the target app is
            # foreground. The OR covers UWP (CoreWindow GA_ROOT != tracked
            # ApplicationFrameWindow, so point matching alone fails) and the
            # first click that raises a background target window.
            # `contradiction` already established that the point is inside a
            # tracked window — this pointer-based check uses the same
            # top_window_at() that just disagreed, so letting it drop the event
            # here would undo the branch above.
            if not (contradiction or self._point_is_target(x, y) or self._foreground_is_target()):
                log(f"[skip] {action} top={top_window_at(x, y)} "
                    f"fg={foreground_top_window()} x={x} y={y} — not target app")
                return

        # Popup detection: element belongs to a top-level window that is NOT the main app window
        root_hwnd = elem.get("rootHwnd", 0)
        # Electron fallback: only when rootHwnd=0 AND the event's pointer is over a
        # known target window. This prevents native dialogs (e.g. "폴더 열기") whose
        # some elements return hwnd=0 from being misclassified as Electron.
        if root_hwnd == 0 and x is not None:
            top_at_point = top_window_at(x, y)
            if top_at_point in self.target_hwnds:
                root_hwnd_for_class = top_at_point
            else:
                root_hwnd_for_class = 0   # unknown window — don't force Electron
        else:
            root_hwnd_for_class = root_hwnd
        is_popup = (
            bool(root_hwnd)
            and root_hwnd in self._popup_hwnds
        )
        popup_title = ""
        if is_popup:
            try:
                popup_title = win32gui.GetWindowText(root_hwnd)
            except Exception:
                popup_title = elem.get("windowTitle", "")

        # Electron detection: annotate only — do NOT override UIA locator.
        # UIA properties (automationId, name, xpath) remain primary.
        # The captured (x, y) / (relX, relY) serve as fallback_coordinates in
        # generated code (try el.click() first → coordinate touch action on failure).
        is_electron = self._is_electron(root_hwnd_for_class)

        event = {
            "action": action,
            "element": {
                "name": elem.get("name", ""),
                "automationId": elem.get("automationId", ""),
                "className": elem.get("className", ""),
                "controlType": elem.get("controlType", ""),
                "windowTitle": elem.get("windowTitle", ""),
                "xpath": elem.get("xpath", ""),
                "isInputField": elem.get("controlType", "") in INPUT_CONTROL_TYPES,
                "locatorFallback": elem.get("locatorFallback", ""),   # NEW
                "locatorStrategy": elem.get("locatorStrategy", ""),
                "locatorValue": elem.get("locatorValue", ""),
                # Which fallback (if any) scavenged "name" when standard UIA
                # Name/AutomationId were both empty (2026-08-08,
                # UIAInspector.describe()'s LegacyIAccessible/HelpText
                # fallback block). "" = real UIA CurrentName. Whitelist field
                # — same trap as comboItemIndex below: leave this out and it
                # vanishes silently before reaching server.js.
                "nameSource": elem.get("nameSource", ""),
                # 부모(이름 있는 조상) + 동일 ControlType 형제 순번 (2026-08-08,
                # UIAInspector._ancestor_sibling_selector) — comboItemIndex와
                # 완전히 같은 화이트리스트 함정: 여기 빠뜨리면 캡처 로그엔
                # 찍히는데 서버엔 조용히 전달 안 됨.
                "ancestorAutomationId": elem.get("ancestorAutomationId", ""),
                "ancestorName": elem.get("ancestorName", ""),
                "ancestorClassName": elem.get("ancestorClassName", ""),
                "ancestorHwnd": elem.get("ancestorHwnd", 0),
                "ancestorSiblingIndex": elem.get("ancestorSiblingIndex"),
                "ancestorSiblingCount": elem.get("ancestorSiblingCount"),
                "ancestorItemControlTypeId": elem.get("ancestorItemControlTypeId"),
                # 2026-08-11: 이 ancestor+index가 의심스러운 self-heal 직후에
                # 만들어졌다는 표시(§CLAUDE.md 하드룰: 조용한 좌표/추측 폴백
                # 대신 명시적 FAIL) — 같은 화이트리스트 함정, 빠뜨리면
                # server.js가 항상 False로 보고 정상 셀렉터처럼 재생해버린다.
                "ambiguousCapture": elem.get("ambiguousCapture", False),
                # anchor 기반 relative XPath (유니크 id/name 없는 요소 전용,
                # 2026-07-10 지시) — codegen이 //*[@AutomationId=anchor]/path 생성.
                "anchorId": elem.get("anchorId", ""),
                "anchorPath": elem.get("anchorPath", ""),
                "rect": elem.get("rect"),   # DIAGNOSTIC — see UIAInspector.describe()
                # 컨트롤의 NativeWindowHandle (2026-07-29) — server.js가
                # automationId가 이 값과 같은지 판별해 hwnd 기반(Delphi/VCL
                # 앱에서 실행마다 바뀌는) automationId를 셀렉터에서 걸러내는
                # 근거로 쓴다(isWindowHandleId). 화이트리스트라 여기 빠뜨리면
                # server.js로 조용히 전달 안 됨 — 2026-07-13에 expandCollapse
                # 필드로 이미 한 번 겪은 함정.
                "hwnd": elem.get("hwnd", 0),
                # ComboBox 드롭다운/메뉴바 MenuItem/트리 +- 토글 판별
                # (2026-07-13, UIAInspector.has_expand_collapse) — codegen이
                # 일반 클릭 대신 osExpandCollapse.ps1 경로를 taken다.
                "expandCollapse": bool(elem.get("expandCollapse", False)),
                # 열린 드롭다운의 이름 없는 항목 위치 (2026-07-31,
                # UIAInspector.combo_item_self/open_dropdown_item_at) —
                # 여기 화이트리스트에 빠지면 위 주석 그대로 재현된다: 실측
                # 확인됨(HeidiSQL 재생 로그, comboItemIndex가 캡처 시점에는
                # 잡혔는데 서버에 도착한 이벤트에는 없어 '4:expandCollapse'로
                # itemIndex 없이 생성됨).
                "comboItemIndex": elem.get("comboItemIndex"),
                "comboItemCount": elem.get("comboItemCount"),
                "comboItemName": elem.get("comboItemName", ""),
                # 열린 팝업 메뉴의 이름 없는 항목 위치 (2026-08-04,
                # UIAInspector.menu_item_self/open_menu_item_at) — comboItem*
                # 바로 위 주석의 함정과 완전히 같은 화이트리스트라 여기 빠뜨리면
                # 캡처 로그엔 찍히는데 서버엔 조용히 전달 안 됨.
                "menuItemIndex": elem.get("menuItemIndex"),
                "menuItemCount": elem.get("menuItemCount"),
                "menuItemName": elem.get("menuItemName", ""),
                # 이 창을 처음 봤을 때의 제목 (2026-08-03, self._first_titles).
                # windowTitle은 앱이 실행 중 창 이름을 바꾸면 재생 때 재현
                # 불가능한 값이 된다("...: Unnamed-14"). server.js
                # canonicalizeWindowTitles()가 이 값을 병합 대상으로 쓴다.
                # 위 주석대로 화이트리스트라 여기 빠뜨리면 조용히 전달 안 됨.
                "stableWindowTitle": self._first_titles.get(root_hwnd, ""),
                # True when this element lives inside an embedded-Chromium
                # view. server.js uses it to reject render-counter
                # AutomationIds WITHOUT touching WinForms designer ids of the
                # same shape (isRenderCounterId). Whitelist field — leaving it
                # out here means it never reaches the server, the trap this
                # dict has already sprung twice (expandCollapse 2026-07-13,
                # comboItemIndex 2026-07-31).
                "isWebContent": bool(elem.get("isWebContent", False)),
                # 숫자 UIA ControlType (2026-08-10, FileZilla "C:" 트리 vs
                # 리스트 혼동 실측) — describe()가 이미 계산해두지만
                # (_ancestor_sibling_selector가 내부에서 씀) 여기 화이트리스트에
                # 없어서 서버로는 안 넘어갔던 필드. automationId/className이
                # 둘 다 없는(owner-drawn) 행에서 같은 Name이 여러 컨트롤에
                # 걸쳐 있을 때(트리의 "C:"와 리스트의 "C:") server.js의
                # comSafeTarget()이 이 값으로 추가 AND 조건을 걸어 구분한다.
                "controlTypeId": elem.get("controlTypeId"),
            },
            "timestamp": time.time(),
            "app": self.session.get("appName", ""),
            "platform": self.session.get("platform", "Windows"),
        }
        # Popup annotation
        if is_popup:
            event["isPopup"] = True
            event["popupTitle"] = popup_title
        # Electron annotation
        if is_electron:
            event["isElectron"] = True
        raw_root = elem.get("rootHwnd", 0)
        if raw_root:
            event["rootHwndHex"] = format(raw_root, 'X')   # bare uppercase hex, no 0x prefix
        if value is not None:
            event["value"] = value
        if delta is not None:
            event["delta"] = delta
        if extra:
            event.update(extra)   # e.g. scrollTarget
        if x is not None:
            event["x"], event["y"] = int(x), int(y)
            if end is not None:
                event["endX"], event["endY"] = int(end[0]), int(end[1])
            root_hwnd_for_rect = elem.get("rootHwnd", 0)
            if not root_hwnd_for_rect and x is not None:
                root_hwnd_for_rect = top_window_at(int(x), int(y))   # 포인터 아래 실제 창 — 결정적
            if not root_hwnd_for_rect and self.target_hwnds:
                root_hwnd_for_rect = self._pick_frame_hwnd()    # 최후 폴백
            rect = self._get_win_rect(root_hwnd_for_rect)
            if rect is not None:
                win_left, win_top, win_w, win_h = rect
                rel_x = max(0, int(x) - win_left)
                rel_y = max(0, int(y) - win_top)
                if int(x) < win_left or int(y) < win_top:
                    log(f"[coords] clamped negative rel coords for {action} "
                        f"abs=({int(x)},{int(y)}) win=({win_left},{win_top})")
                event["relX"] = rel_x
                event["relY"] = rel_y
                event["winLeft"] = win_left
                event["winTop"] = win_top
                event["winWidth"] = win_w
                event["winHeight"] = win_h
                if end is not None:
                    event["endRelX"] = max(0, int(end[0]) - win_left)
                    event["endRelY"] = max(0, int(end[1]) - win_top)

        # 명시적 윈도우 세그먼트 경계 신호 (2026-07-16) — codegen이 title diff가
        # 아니라 hwnd 기반의 확실한 경계를 받도록 소스에서 태깅. 같은 창이
        # 연속되면(hex 불변) 안 붙음 — 구버전 레코딩과의 하위호환을 위해 이
        # 필드가 없는 이벤트는 server.js가 기존 rootHwndHex diff 폴백을 쓴다.
        cur_hwnd_hex = event.get("rootHwndHex", "")
        if cur_hwnd_hex and cur_hwnd_hex != self._last_emitted_hwnd_hex:
            event["newWindowSegment"] = True
        if cur_hwnd_hex:
            self._last_emitted_hwnd_hex = cur_hwnd_hex

        # screenId: sanitized window title — groups events by UI context
        raw_title = event["element"].get("windowTitle", "") or self.session.get("appName", "")
        screen_id = re.sub(r'[^a-z0-9]+', '_', raw_title.lower()).strip('_') or "unknown"
        event["screenId"] = screen_id

        self.event_count += 1
        event["index"] = self.event_count
        pt = f" pt=({int(x)},{int(y)})" if x is not None else ""
        # 2026-08-05 (사용자 보고 "파일 -> 사이트 관리자를 눌렀는데 파일만 2번
        # 누른 걸로 나온다"): 콤보/메뉴 항목 선택 이벤트는 설계상 element가
        # **트리거**의 것이고(_menu_item_from_cache/_dropdown_item_from_cache가
        # describe(trigger)를 쓴다), 고른 항목은 menuItemIndex/menuItemName에
        # 따로 실린다 — 그런데 이 로그 줄은 name(=트리거 이름)만 찍어서
        # 트리거 클릭과 항목 선택이 화면상 완전히 똑같이 보였다. 캡처 데이터는
        # 정확한데 로그만 오해를 부르는 상황이라, 실제로 무엇을 골랐는지를
        # 함께 찍는다.
        _el = event['element']
        _pick = ""
        if _el.get('menuItemIndex') is not None:
            _pick = (f" -> menu item #{_el['menuItemIndex']}"
                     f"/{_el.get('menuItemCount')} '{(_el.get('menuItemName') or '')[:40]}'")
        elif _el.get('comboItemIndex') is not None:
            _pick = (f" -> combo item #{_el['comboItemIndex']}"
                     f"/{_el.get('comboItemCount')} '{(_el.get('comboItemName') or '')[:40]}'")
        log(f"#{self.event_count} {action:11s} "
            f"id='{_el['automationId']}' "
            f"name='{_el['name'][:30]}'{_pick}"
            f" rect={_el.get('rect')}{pt}"
            + (f" value='{value}'" if value else ""))
        try:
            requests.post(EXPRESS_EVENTS_URL, json=event, timeout=3)
        except Exception as e:
            log(f"WARN: could not POST to bridge: {e}")


# ----------------------------------------------------------------------------
# HTTP control server (port 4444)
# ----------------------------------------------------------------------------
recorder = Recorder()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # silence default logging
        pass

    def _json(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/status":
            self._json(200, {
                "online": True,
                "recording": recorder.recording,
                "eventCount": recorder.event_count,
                "isAdmin": bool(ctypes.windll.shell32.IsUserAnAdmin()),
            })
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            body = {}

        if self.path == "/start":
            ok, msg = recorder.start(
                body.get("appName", "App"),
                body.get("exePath", ""),
                body.get("platform", "Windows"),
            )
            self._json(200 if ok else 400, {"ok": ok, "message": msg})
        elif self.path == "/stop":
            ok, msg = recorder.stop()
            self._json(200 if ok else 400, {"ok": ok, "message": msg})
        else:
            self._json(404, {"error": "not found"})


def _enable_per_monitor_dpi_awareness():
    """Raise this process to per-monitor DPI awareness. Unaware (default)
    processes get coordinates auto-scaled by the OS to the primary monitor's
    DPI, which desyncs pynput's raw cursor position from the UIA element
    rects hit-tested at capture time (confirmed 2026-07-13 on a 125%-scaled
    PuTTY capture: consistent ~1.25x pynput/cursor deltas on every click).
    Must run before any window/DC is created by this process — main() is the
    first thing that runs, so this call sits at its very top."""
    try:
        # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 (Win10 1703+)
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        return
    except Exception:
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
    except Exception as e:
        log(f"[diag-dpi] failed to set per-monitor DPI awareness: {e}")


def _build_marker():
    """mtime + short hash of this file, printed at startup.

    agent.py has no hot reload (CLAUDE.md §5) — a running process keeps
    executing the code it loaded at start even after the file on disk
    changes. Without this, a log pasted after an edit is silent about
    whether the edit was actually running, and debugging a fix's effect
    degrades into re-litigating logs that never exercised the fix at all.
    """
    try:
        path = os.path.abspath(__file__)
        mtime = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(os.path.getmtime(path)))
        with open(path, "rb") as f:
            sha = hashlib.sha256(f.read()).hexdigest()[:8]
        return f"mtime={mtime} sha256={sha}"
    except Exception as e:
        return f"unavailable ({e})"


def main():
    _enable_per_monitor_dpi_awareness()
    is_admin = bool(ctypes.windll.shell32.IsUserAnAdmin())
    log(f"Capture agent listening on http://localhost:{AGENT_PORT}")
    log(f"build: agent.py {_build_marker()}")
    log(f"Administrator rights: {'YES' if is_admin else 'NO  <-- element properties will be EMPTY!'}")
    if not is_admin:
        log("Re-run from an Administrator PowerShell for full element inspection.")
    try:
        awareness = ctypes.c_int()
        ctypes.windll.shcore.GetProcessDpiAwareness(0, ctypes.byref(awareness))
        log(f"[diag-dpi] process DPI awareness={awareness.value} (0=unaware 1=system 2=per-monitor)")
    except Exception as e:
        log(f"[diag-dpi] GetProcessDpiAwareness failed: {e}")
    ThreadingHTTPServer(("127.0.0.1", AGENT_PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
