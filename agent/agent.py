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

    def element_at(self, x, y):
        pt = wintypes.POINT(int(x), int(y))
        elem = self._uia.ElementFromPoint(pt)
        if elem is not None:
            try:
                if not elem.CurrentAutomationId:
                    deeper = self._deepen(elem, int(x), int(y))
                    if deeper is not None:
                        try:
                            if deeper.CurrentAutomationId or deeper.CurrentName:
                                return deeper
                        except Exception:
                            pass
            except Exception:
                pass
        return elem

    def _deepen(self, elem, x, y, depth=0):
        """Walk ControlView tree to find the deepest child containing (x, y)."""
        if depth >= 5:
            return None
        try:
            walker = self._uia.ControlViewWalker
            child = walker.GetFirstChildElement(elem)
            while child is not None:
                try:
                    rect = child.CurrentBoundingRectangle
                    if rect.left <= x <= rect.right and rect.top <= y <= rect.bottom:
                        deeper = self._deepen(child, x, y, depth + 1)
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

        self._mouse_listener = None
        self._kb_listener = None
        self._worker = None
        self._stop_flag = threading.Event()
        self._watcher = None          # background window-discovery thread

        # worker-side state
        self._last_left_click = None  # timing/pos of previous left click (dbl-click)
        self._pending_scroll = None
        self._type_buffer = ""
        self._type_elem = None       # element info captured at first keystroke

    # ---------------- control ----------------
    def start(self, app_name, exe_path, platform):
        if self.recording:
            return False, "Already recording"
        self.session = {"appName": app_name, "exePath": exe_path, "platform": platform}
        self.event_count = 0
        self.target_hwnds = set()
        self._popup_hwnds = set()

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
        if not self.recording or not pressed or injected:
            return
        self.raw_queue.put({"kind": "click", "x": x, "y": y,
                            "button": button.name, "ts": time.time()})

    def _on_scroll(self, x, y, dx, dy, injected=False):
        if not self.recording or injected:
            return
        self.raw_queue.put({"kind": "scroll", "x": x, "y": y,
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
        if app_key:
            try:
                raw_title = win32gui.GetWindowText(top)
                title_key = re.sub(r'[^a-z0-9]', '', raw_title.lower())
                if app_key in title_key or title_key in app_key:
                    self.target_hwnds.add(top)
                    log(f"[target] title-match hwnd={top} title='{raw_title}' accepted")
                    return True
            except Exception:
                pass
        return False

    def _foreground_is_target(self):
        if not self.target_hwnds:
            return True
        return foreground_top_window() in self.target_hwnds

    def _discover_target_windows(self):
        """Poll up to DISCOVER_TIMEOUT for the window(s) the launched app opens.
        Primary signal: top-level windows that appeared AFTER launch (diff vs
        the pre-launch snapshot). Also accept windows owned by the launch pid
        (classic Win32). Fallback: the current foreground top-level window."""
        launch_pid = self.proc.pid if self.proc else 0
        deadline = time.time() + DISCOVER_TIMEOUT
        while time.time() < deadline and not self._stop_flag.is_set():
            current = visible_toplevel_windows()
            found = set()
            for hwnd in current:
                # newly-appeared, titled top-level window (UWP + most apps)
                if hwnd not in self._pre_hwnds:
                    try:
                        if win32gui.GetWindowText(hwnd).strip():
                            found.add(hwnd)
                    except Exception:
                        pass
                # window owned by the launched process (classic Win32)
                if launch_pid and pid_of_hwnd(hwnd) == launch_pid:
                    found.add(hwnd)
            if found:
                self.target_hwnds |= found
                titles = []
                for h in self.target_hwnds:
                    try:
                        titles.append(win32gui.GetWindowText(h))
                    except Exception:
                        titles.append("")
                log(f"[target] hwnds={self.target_hwnds} titles={titles}")
                return
            time.sleep(0.2)

        fg = foreground_top_window()
        if fg:
            self.target_hwnds.add(fg)
            log(f"[target] fallback foreground hwnd={fg} "
                f"title='{win32gui.GetWindowText(fg)}'")
        else:
            log("[target] discovery failed — filtering disabled (accept all)")

    def _watch_windows(self):
        """Background thread: poll for new top-level windows owned by target
        process PIDs and auto-add them to target_hwnds. Fixes the bug where
        popup/child windows opened after recording started were silently
        filtered by _point_is_target()."""
        launch_pid = self.proc.pid if self.proc else None

        while not self._stop_flag.is_set():
            try:
                target_pids = {launch_pid} if launch_pid else set()
                for hwnd in list(self.target_hwnds):
                    p = pid_of_hwnd(hwnd)
                    if p:
                        target_pids.add(p)
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

            if btn == "right":
                self._flush_pending_click()
                self._emit_pointer_event("rightClick", x, y, ins)
                return

            # Every left click is recorded individually (preserves repeated
            # presses like "9999" -> num9Button x4). A genuine fast double-click
            # is recognised IN ADDITION, never by merging/dropping the clicks.
            elem = self._inspect(ins, x, y)
            self._emit("click", elem, x=x, y=y)

            ll = self._last_left_click
            if (ll and ts - ll["ts"] <= DOUBLE_CLICK_INTERVAL
                    and abs(x - ll["x"]) <= DOUBLE_CLICK_RADIUS
                    and abs(y - ll["y"]) <= DOUBLE_CLICK_RADIUS):
                self._emit("doubleClick", elem, x=x, y=y)
                self._last_left_click = None  # consume; avoid chaining triples
            else:
                self._last_left_click = {"x": x, "y": y, "ts": ts}
            return

        if kind == "scroll":
            self._flush_type_buffer()
            self._flush_pending_click()
            x, y, ts = item["x"], item["y"], item["ts"]
            if self._pending_scroll is None:
                self._pending_scroll = {"x": x, "y": y, "ts": ts,
                                        "amount": item["dy"],
                                        "elem": self._inspect(ins, x, y)}
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
                # Preserve newline so sendKeys("...\n") can replay it
                if self._type_buffer:
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

    def _flush_pending_click(self):
        # Left clicks are emitted immediately; this only ends the open
        # double-click window so an intervening event can't pair across it.
        self._last_left_click = None

    def _flush_pending_scroll(self):
        ps, self._pending_scroll = self._pending_scroll, None
        if ps:
            self._emit("scroll", ps["elem"], x=ps["x"], y=ps["y"],
                       value=str(ps["amount"]), delta=ps["amount"])

    def _flush_type_buffer(self):
        text, self._type_buffer = self._type_buffer, ""
        elem, self._type_elem = self._type_elem, None
        if text:
            self._emit("type", elem or {}, value=text)

    def _emit_pointer_event(self, action, x, y, ins):
        self._emit(action, self._inspect(ins, x, y), x=x, y=y)

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

    def _emit_session_meta(self):
        """Emit a session_meta event with initial window geometry."""
        hwnd = next(iter(self.target_hwnds), 0)
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

    def _emit(self, action, elem, x=None, y=None, value=None, delta=None):
        elem = elem or {}
        # Application filtering by top-level window handle.
        # Pointer events carry (x, y) — filter by the window under the point.
        # `type` events have no point; they were already gated on the
        # foreground window being the target at capture time.
        if action != "type" and x is not None:
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

        # Electron detection: force coordinate locator for Electron apps
        is_electron = self._is_electron(root_hwnd)
        if is_electron:
            elem = dict(elem)  # don't mutate the passed-in dict
            elem["locatorFallback"] = "coordinate"
            elem["locatorStrategy"] = "coordinate"

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
        if x is not None:
            event["x"], event["y"] = int(x), int(y)
            root_hwnd_for_rect = elem.get("rootHwnd", 0)
            if not root_hwnd_for_rect and self.target_hwnds:
                root_hwnd_for_rect = next(iter(self.target_hwnds))
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

        # screenId: sanitized window title — groups events by UI context
        raw_title = event["element"].get("windowTitle", "") or self.session.get("appName", "")
        screen_id = re.sub(r'[^a-z0-9]+', '_', raw_title.lower()).strip('_') or "unknown"
        event["screenId"] = screen_id

        self.event_count += 1
        event["index"] = self.event_count
        log(f"#{self.event_count} {action:11s} "
            f"id='{event['element']['automationId']}' "
            f"name='{event['element']['name'][:30]}'"
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


def main():
    is_admin = bool(ctypes.windll.shell32.IsUserAnAdmin())
    log(f"Capture agent listening on http://localhost:{AGENT_PORT}")
    log(f"Administrator rights: {'YES' if is_admin else 'NO  <-- element properties will be EMPTY!'}")
    if not is_admin:
        log("Re-run from an Administrator PowerShell for full element inspection.")
    ThreadingHTTPServer(("127.0.0.1", AGENT_PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
