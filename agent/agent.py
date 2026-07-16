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
DRAG_MIN_DIST = 10             # pixels — press-to-release distance above which
                                # a left click is recorded as a drag instead
                                # (deliberately above DOUBLE_CLICK_RADIUS so a
                                # normal double-click's small jitter never
                                # misfires as a drag)
SCROLL_FLUSH_IDLE = 0.40       # seconds of no scrolling -> emit scroll event
QUEUE_POLL_TIMEOUT = 0.20      # worker wakeup interval for pending flushes
DISCOVER_TIMEOUT = 5.0         # seconds to wait for the target window to appear

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


# ----------------------------------------------------------------------------
# UI Automation helpers (run ONLY on the worker thread - COM apartment there)
# ----------------------------------------------------------------------------
class UIAInspector:
    """Thin wrapper around the raw IUIAutomation COM interface."""

    def __init__(self):
        import comtypes
        import comtypes.client
        comtypes.CoInitialize()
        # Generates/loads the UIAutomationClient wrapper module
        self._uia = comtypes.client.CreateObject(
            "{ff48dba4-60ef-4201-aa87-54103eef594e}",
            interface=comtypes.client.GetModule("UIAutomationCore.dll").IUIAutomation,
        )

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
                    if cur.CurrentControlType in self.ROW_CONTROL_TYPES and cur.CurrentName:
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

    def element_at(self, x, y):
        pt = wintypes.POINT(int(x), int(y))
        elem = self._uia.ElementFromPoint(pt)
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
                    deeper = self._deepen(elem, int(x), int(y))
                    if deeper is not None:
                        try:
                            if deeper.CurrentAutomationId or deeper.CurrentName:
                                elem = deeper
                        except Exception:
                            pass
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
                    return row
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
        None when nothing better than the window itself is found."""
        try:
            hwnd = foreground_top_window()
            if not hwnd:
                return None
            root = self._uia.ElementFromHandle(hwnd)
            if root is None:
                return None
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
                if aid and "." not in aid and not is_slot_index:
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
                if cur is None:
                    return 0
                try:
                    h = cur.CurrentNativeWindowHandle
                    if h:
                        return h
                except Exception:
                    pass
                cur = walker.GetParent(cur)
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
    def describe(elem):
        """Extract metadata from a UIA element. Never raises - returns partial
        data on failure (reliability requirement)."""
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
        try:
            info["controlType"] = UIA_CONTROL_TYPES.get(
                elem.CurrentControlType, str(elem.CurrentControlType)
            )
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
        # Fallback: foreground window for UWP elements where hwnd=0
        # (UWP elements often return CurrentNativeWindowHandle=0, leaving
        #  windowTitle empty, causing buildUserPrompt to fall back to the
        #  English appName instead of the localized title like "계산기")
        if not info["windowTitle"]:
            try:
                fg = ctypes.windll.user32.GetForegroundWindow()
                if fg:
                    info["windowTitle"] = win32gui.GetWindowText(fg)
            except Exception:
                pass

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

    def _e(child, _):
        try:
            p = pid_of_hwnd(child)
            log(f"[probe:{tag}]   child={child} cls='{win32gui.GetClassName(child)}' "
                f"pid={p} img='{image_path_of_pid(p)}'")
        except Exception:
            pass
        return True

    try:
        win32gui.EnumChildWindows(hwnd, _e, None)
    except Exception:
        pass


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
        self._pre_hwnds = set()      # visible top-levels snapshotted before launch
        # Wall-clock time _discover_target_windows() finished resolving
        # target_hwnds (real match or fallback). Mouse/keyboard hooks are live
        # from recording=True, i.e. BEFORE this resolves — any raw event
        # captured earlier (item["ts"] < this) was captured against a
        # not-yet-final target_hwnds (the app window may not even exist yet)
        # and is dropped in _emit(), regardless of when it's later processed.
        self._discovery_done_ts = 0.0
        self._probed_skip = False    # one-shot diagnostic probe on first mismatched-window click

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
        self._probed_skip = False
        self._last_emitted_hwnd_hex = ""

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
        # Both press ("click") and release ("release") are enqueued — still
        # enqueue-only, no UIA/COM here. The worker pairs them to tell a plain
        # click apart from a press-hold-move-release drag (text selection).
        self.raw_queue.put({"kind": "click" if pressed else "release", "x": x, "y": y,
                            "cursor_x": cursor_pt.x, "cursor_y": cursor_pt.y,
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
            # Ordinary Win32 sibling/child dialog belonging to a tracked PID
            # (e.g. a freshly-opened Site Manager/Quickconnect dialog) — self
            # -heal immediately via PID match instead of waiting on
            # _watch_windows()'s 0.5s poll, which can otherwise drop every
            # keystroke typed in that window during the gap (2026-07-13).
            if pid_of_hwnd(fg) in self._target_pids():
                self.target_hwnds.add(fg)
                self._popup_hwnds.add(fg)
                log(f"[target] lazy PID-match {fg} — added (keyboard, "
                    "pre-empts 0.5s watcher poll)")
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

    def _discover_target_windows(self):
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
                self._discovery_done_ts = time.time()
                return
            time.sleep(0.2)

        fg = foreground_top_window()
        if fg:
            self.target_hwnds.add(fg)
            log(f"[target] fallback foreground hwnd={fg} "
                f"title='{win32gui.GetWindowText(fg)}'")
        else:
            log("[target] discovery failed — filtering disabled (accept all)")
        self._discovery_done_ts = time.time()

    def _watch_windows(self):
        """Background thread: poll for new top-level windows owned by target
        process PIDs and auto-add them to target_hwnds. Fixes the bug where
        popup/child windows opened after recording started were silently
        filtered by _point_is_target()."""
        while not self._stop_flag.is_set():
            try:
                target_pids = self._target_pids()
                for hwnd in visible_toplevel_windows():
                    if hwnd in self.target_hwnds:
                        continue
                    if pid_of_hwnd(hwnd) in target_pids:
                        self.target_hwnds.add(hwnd)
                        self._popup_hwnds.add(hwnd)
                        try:
                            title = win32gui.GetWindowText(hwnd)
                            log(f"[watcher] added hwnd={hwnd} title='{title}'")
                        except Exception:
                            log(f"[watcher] added hwnd={hwnd}")
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

        self._discover_target_windows()
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
        self._flush_pending_click()
        self._flush_pending_scroll()
        log("Worker finished")

    def _handle(self, item, ins):
        kind = item["kind"]

        if kind == "stop":
            self._flush_type_buffer()
            self._flush_pending_click()
            self._flush_pending_scroll()
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
                self._flush_pending_click()
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
                self._emit_click_from_press(self._pending_press)
            elem = self._inspect(ins, cx, cy)
            # DIAGNOSTIC (kept for verification) — pynput_pt vs cursor_pt and
            # the resolved element name, so a re-recording can be eyeballed.
            gap = time.time() - ts
            delta = (x - cx, y - cy)
            log(f"[diag-click] pynput_pt=({x},{y}) cursor_pt=({cx},{cy}) "
                f"delta={delta} gap={gap:.4f}s "
                f"elem_name='{elem.get('name', '')}' elem_rect={elem.get('rect')}")
            self._pending_press = {"x": cx, "y": cy, "ts": ts, "elem": elem}
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
                self._emit_click_from_press(press)
            return

        if kind == "scroll":
            self._flush_type_buffer()
            self._flush_pending_click()
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
            self._flush_pending_click()
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

    def _inspect(self, ins, x, y):
        try:
            elem = ins.element_at(x, y)
            info = ins.describe(elem)
            light_dismiss = info.get("automationId") == "Light Dismiss"
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
                if (elem_hwnd and elem_hwnd not in self.target_hwnds
                        and elem_hwnd not in self._popup_hwnds):
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
                    # rejected below.
                    if pid_of_hwnd(elem_hwnd) in self._target_pids():
                        self.target_hwnds.add(elem_hwnd)
                        self._popup_hwnds.add(elem_hwnd)
                        log(f"[inspect] PID self-heal hwnd={elem_hwnd} "
                            f"(name={info.get('name')!r}) — accepted "
                            "(pre-empts 0.5s watcher poll)")
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
                left, top, right, bottom = info["rect"]
                if not (left <= x <= right and top <= y <= bottom):
                    log(f"[inspect] pt=({x},{y}) outside adopted rect={info['rect']} "
                        f"(id={info.get('automationId')!r} name={info.get('name')!r}) "
                        "— dropping selector")
                    light_dismiss = True
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
                under = ins.element_under_overlay(x, y)
                if under is not None:
                    resolved = ins.describe(under)
                    if resolved.get("automationId") or resolved.get("name"):
                        info = resolved
                        elem = under   # keep anchor/row logic consistent below
                        light_dismiss = False
                        log(f"[inspect] resolved under light-dismiss at ({x},{y}): "
                            f"id={info.get('automationId')!r} name={info.get('name')!r} "
                            f"type={info.get('controlType')!r}")
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
                    row_info = ins.describe(row)
                    if row_info.get("name") or row_info.get("automationId"):
                        info = row_info
                        elem = row
                        treeitem_glyph_fallback = True
                        log(f"[inspect] Tree-center fallback narrowed to row "
                            f"TreeItem name={info.get('name')!r}")
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

    def _emit_click_from_press(self, press):
        """Emit click (+ doubleClick if paired) for a completed left press.
        Shared by the release handler and the stale-press flush so both
        paths reproduce identical click/double-click semantics."""
        cx, cy, ts, elem = press["x"], press["y"], press["ts"], press["elem"]
        # Every left click is recorded individually (preserves repeated
        # presses like "9999" -> num9Button x4). A genuine fast double-click
        # is recognised IN ADDITION, never by merging/dropping the clicks.
        self._emit("click", elem, x=cx, y=cy, ts=ts)
        self._last_click_xy = (cx, cy)

        ll = self._last_left_click
        if (ll and ts - ll["ts"] <= DOUBLE_CLICK_INTERVAL
                and abs(cx - ll["x"]) <= DOUBLE_CLICK_RADIUS
                and abs(cy - ll["y"]) <= DOUBLE_CLICK_RADIUS):
            self._emit("doubleClick", elem, x=cx, y=cy, ts=ts)
            self._last_left_click = None  # consume; avoid chaining triples
        else:
            self._last_left_click = {"x": cx, "y": cy, "ts": ts}

    def _flush_pending_click(self):
        # A pending left press with no release yet (e.g. focus moved before
        # button-up, or the release was lost) must not be silently dropped —
        # emit it as a plain click, same as a completed press+release would.
        if self._pending_press is not None:
            press, self._pending_press = self._pending_press, None
            self._emit_click_from_press(press)
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
            if not (self._point_is_target(x, y) or self._foreground_is_target()):
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
                # anchor 기반 relative XPath (유니크 id/name 없는 요소 전용,
                # 2026-07-10 지시) — codegen이 //*[@AutomationId=anchor]/path 생성.
                "anchorId": elem.get("anchorId", ""),
                "anchorPath": elem.get("anchorPath", ""),
                "rect": elem.get("rect"),   # DIAGNOSTIC — see UIAInspector.describe()
                # ComboBox 드롭다운/메뉴바 MenuItem/트리 +- 토글 판별
                # (2026-07-13, UIAInspector.has_expand_collapse) — codegen이
                # 일반 클릭 대신 osExpandCollapse.ps1 경로를 taken다.
                "expandCollapse": bool(elem.get("expandCollapse", False)),
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
        log(f"#{self.event_count} {action:11s} "
            f"id='{event['element']['automationId']}' "
            f"name='{event['element']['name'][:30]}'"
            f" rect={event['element'].get('rect')}{pt}"
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


def main():
    _enable_per_monitor_dpi_awareness()
    is_admin = bool(ctypes.windll.shell32.IsUserAnAdmin())
    log(f"Capture agent listening on http://localhost:{AGENT_PORT}")
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
