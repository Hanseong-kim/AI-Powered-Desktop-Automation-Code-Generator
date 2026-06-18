"""
AI-Powered Desktop Automation Code Generator - Python Capture Agent
====================================================================
- Launches the target .exe and hooks global mouse/keyboard input (pynput)
- Reads element details from Windows UI Automation (pywinauto / comtypes)
- Filters events to the target application only
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
import subprocess
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import psutil
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
INPUT_CONTROL_TYPES = {"Edit", "Document", "ComboBox"}

GA_ROOT = 2                    # GetAncestor flag
UIA_Invoke_InvokedEventId = 20009
TreeScope_Subtree = 4


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
            "pid": 0,
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
            info["pid"] = elem.CurrentProcessId or 0
        except Exception:
            pass

        # Window title: walk up to the top-level window
        hwnd = info["hwnd"]
        try:
            if hwnd:
                root = ctypes.windll.user32.GetAncestor(hwnd, GA_ROOT)
                info["windowTitle"] = win32gui.GetWindowText(root or hwnd)
        except Exception:
            pass
        # Fallback: foreground window for UWP elements where hwnd=0
        # (UWP elements often return CurrentNativeWindowHandle=0, leaving windowTitle empty,
        #  causing buildUserPrompt to fall back to the English appName instead of the
        #  localized title like "계산기")
        if not info["windowTitle"]:
            try:
                fg = ctypes.windll.user32.GetForegroundWindow()
                if fg:
                    info["windowTitle"] = win32gui.GetWindowText(fg)
            except Exception:
                pass

        # Locator / XPath from the most stable identifier
        if info["automationId"]:
            info["xpath"] = f'//*[@AutomationId="{info["automationId"]}"]'
        elif info["name"]:
            info["xpath"] = f'//*[@Name="{info["name"]}"]'
        elif info["className"]:
            info["xpath"] = f'//*[@ClassName="{info["className"]}"]'
        return info


def _make_invoke_handler(raw_queue):
    """Factory: lazy-imports IUIAutomationEventHandler so comtypes.gen is
    guaranteed to already exist (GetModule must have run on the calling thread
    before this is called). Stores no external references so GC is safe as
    long as the caller keeps the returned object alive."""
    from comtypes.gen.UIAutomationClient import IUIAutomationEventHandler

    class _Handler(comtypes.COMObject):
        _com_interfaces_ = [IUIAutomationEventHandler]

        def HandleAutomationEvent(self, sender, eventId):
            try:
                info = UIAInspector.describe(sender)
                log(f"[uia-events] invoke id='{info.get('automationId','')}' "
                    f"name='{info.get('name','')[:20]}'")
                raw_queue.put({"kind": "uia_invoke", "elem": info,
                               "ts": time.time()})
            except Exception:
                pass

    return _Handler()


def hwnd_at_point(x, y):
    try:
        return ctypes.windll.user32.WindowFromPoint(wintypes.POINT(int(x), int(y)))
    except Exception:
        return 0


def pid_of_hwnd(hwnd):
    try:
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        return pid
    except Exception:
        return 0


# ----------------------------------------------------------------------------
# Recorder
# ----------------------------------------------------------------------------
class Recorder:
    def __init__(self):
        self.raw_queue = queue.Queue()
        self.recording = False
        self.event_count = 0
        self.session = {}            # appName, exePath, platform
        self.target_exe = ""         # lowercase basename, e.g. "notepad.exe"
        self.target_pids = set()     # launched pid + descendants (lazy refresh)
        self.proc = None
        self.known_window_title = ""  # actual title of target window (may be non-English)
        self._uia_event_thread = None

        self._mouse_listener = None
        self._kb_listener = None
        self._worker = None
        self._stop_flag = threading.Event()

        # worker-side state
        self._pending_click = None   # awaiting possible double-click
        self._pending_scroll = None
        self._type_buffer = ""
        self._type_elem = None       # element info captured at first keystroke

    # ---------------- control ----------------
    def start(self, app_name, exe_path, platform):
        if self.recording:
            return False, "Already recording"
        self.session = {"appName": app_name, "exePath": exe_path, "platform": platform}
        self.target_exe = os.path.basename(exe_path).lower()
        self.event_count = 0
        self.target_pids = set()
        self.known_window_title = ""

        # Launch the target application
        try:
            self.proc = subprocess.Popen([exe_path])
            self.target_pids.add(self.proc.pid)
            log(f"Launched {exe_path} (pid={self.proc.pid})")
        except Exception as e:
            return False, f"Failed to launch '{exe_path}': {e}"

        self._stop_flag.clear()
        self.recording = True

        self._worker = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker.start()

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
        if self._uia_event_thread:
            self._uia_event_thread.stop()
            self._uia_event_thread = None
        log("Recording stopped")
        return True, "Recording stopped"

    # ---------------- hook callbacks (hot path - keep tiny) ----------------
    def _on_click(self, x, y, button, pressed):
        if not self.recording or not pressed:
            return
        self.raw_queue.put({"kind": "click", "x": x, "y": y,
                            "button": button.name, "ts": time.time()})

    def _on_scroll(self, x, y, dx, dy):
        if not self.recording:
            return
        self.raw_queue.put({"kind": "scroll", "x": x, "y": y,
                            "dy": dy, "ts": time.time()})

    def _on_key(self, key):
        if not self.recording:
            return
        item = {"kind": "key", "ts": time.time()}
        if isinstance(key, keyboard.KeyCode) and key.char is not None:
            item["char"] = key.char
        else:
            item["special"] = getattr(key, "name", str(key))
        self.raw_queue.put(item)

    # ---------------- target-app filtering ----------------
    def _is_target_pid(self, pid):
        if not pid:
            return False
        if pid in self.target_pids:
            return True
        try:
            p = psutil.Process(pid)
            proc_name = p.name().lower()
            if proc_name == self.target_exe:
                self.target_pids.add(pid)
                return True
            # accept descendants of the launched process (some apps re-spawn)
            if self.proc and self.proc.pid:
                for anc in p.parents():
                    if anc.pid == self.proc.pid:
                        self.target_pids.add(pid)
                        return True
            # UWP stub match: "calc.exe" launcher exits immediately; the real
            # process is "CalculatorApp.exe".  Match by exe stem as a substring
            # of the actual process name (language-independent).
            target_stem = os.path.splitext(self.target_exe)[0].lower()
            if len(target_stem) >= 3 and target_stem in proc_name:
                self.target_pids.add(pid)
                return True
        except Exception:
            pass
        return False

    def _belongs_to_target(self, hwnd, x=None, y=None, pid=None):
        # 1. UIA process ID — most reliable path for UWP (bypasses hwnd=0 limitation)
        if pid and self._is_target_pid(pid):
            return True
        # 2. hwnd-based PID lookup
        if not hwnd and x is not None:
            hwnd = hwnd_at_point(x, y)
        if self._is_target_pid(pid_of_hwnd(hwnd)):
            return True
        # 3. Window title match (appName or cached real title like "계산기")
        if self._title_matches_app(hwnd):
            return True
        # 4. UWP: WindowFromPoint returns ApplicationFrameHost's HWND.
        #    CalculatorApp's CoreWindow is a child — enumerate children for its PID.
        if hwnd:
            found = [False]
            def _check_child(child_hwnd, _):
                if self._is_target_pid(pid_of_hwnd(child_hwnd)):
                    found[0] = True
                    return False
                return True
            try:
                win32gui.EnumChildWindows(hwnd, _check_child, None)
            except Exception:
                pass
            if found[0]:
                return True
        return False

    def _title_matches_app(self, hwnd):
        if not hwnd:
            return False
        app_name = self.session.get("appName", "").strip()
        if not app_name and not self.known_window_title:
            return False
        try:
            root = ctypes.windll.user32.GetAncestor(hwnd, GA_ROOT) or hwnd
            title = win32gui.GetWindowText(root)
            if not title:
                return False
            def norm(s):
                return "".join(c for c in s.lower() if c.isalnum())
            n_title = norm(title)
            matched = (app_name and norm(app_name) in n_title) or \
                      (self.known_window_title and norm(self.known_window_title) == n_title)
            if matched:
                pid = pid_of_hwnd(root)
                if pid:
                    self.target_pids.add(pid)
                return True
        except Exception:
            pass
        return False

    # ---------------- UWP pid / window discovery ----------------
    def _find_target_window(self):
        """Poll up to 3 s for the target app's top-level HWND.
        Adds matching PIDs (incl. ApplicationFrameHost) to target_pids.
        Returns the HWND (int) or 0 on timeout."""
        deadline = time.time() + 3.0
        while time.time() < deadline:
            result = [0]

            def _enum_top(hwnd, _):
                if not win32gui.IsWindowVisible(hwnd):
                    return True
                wpid = pid_of_hwnd(hwnd)
                if wpid and self._is_target_pid(wpid):
                    self.target_pids.add(wpid)
                    result[0] = hwnd
                    return False

                def _enum_child(child, __):
                    cpid = pid_of_hwnd(child)
                    if cpid and self._is_target_pid(cpid):
                        if wpid:
                            self.target_pids.add(wpid)
                        result[0] = hwnd
                        return False
                    return True

                try:
                    win32gui.EnumChildWindows(hwnd, _enum_child, None)
                except Exception:
                    pass
                return result[0] == 0

            try:
                win32gui.EnumWindows(_enum_top, None)
            except Exception:
                pass

            if result[0]:
                log(f"[discover] target pids: {self.target_pids} hwnd: {result[0]}")
                return result[0]
            time.sleep(0.2)

        log("[discover] timed out — using launch pid only")
        return 0

    # ---------------- worker (UIA lookups + emission happen here) ----------
    def _worker_loop(self):
        try:
            inspector = UIAInspector()
        except Exception:
            log("FATAL: could not initialise UI Automation")
            traceback.print_exc()
            return

        target_hwnd = self._find_target_window()
        if target_hwnd:
            self._uia_event_thread = UIAEventThread(self.raw_queue)
            self._uia_event_thread.start(target_hwnd)

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

        if kind == "uia_invoke":
            # UIA Invoke event: upgrade the pending pynput click if it's still
            # in the double-click hold window, otherwise emit directly.
            if (self._pending_click
                    and time.time() - self._pending_click["ts"] < DOUBLE_CLICK_INTERVAL):
                self._pending_click["uia_elem"] = item["elem"]
            else:
                self._emit("click", item["elem"])
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

            # double-click detection: hold the first click briefly
            pc = self._pending_click
            if (pc and ts - pc["ts"] <= DOUBLE_CLICK_INTERVAL
                    and abs(x - pc["x"]) <= DOUBLE_CLICK_RADIUS
                    and abs(y - pc["y"]) <= DOUBLE_CLICK_RADIUS):
                self._pending_click = None
                self._emit_pointer_event("doubleClick", x, y, ins)
            else:
                self._flush_pending_click()
                self._pending_click = {"x": x, "y": y, "ts": ts,
                                       "elem": self._inspect(ins, x, y),
                                       "uia_elem": None}
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
            if char is None:
                return  # shift/ctrl/arrows etc. - ignored
            if not (0x20 <= ord(char) <= 0x7E):
                return  # non-ASCII (IME/CJK composition) — ignore silently

            # first keystroke of a burst: bind buffer to the focused element
            if not self._type_buffer:
                elem = None
                try:
                    fe = ins.focused_element()
                    elem = ins.describe(fe)
                except Exception:
                    pass
                if elem is None or not self._is_target_pid(pid_of_hwnd(elem.get("hwnd", 0))):
                    # focused element unreadable or not in target app
                    if elem is not None and elem.get("hwnd") and \
                            not self._is_target_pid(pid_of_hwnd(elem["hwnd"])):
                        return  # typing in another app -> ignore
                # Skip keystrokes on non-input controls (e.g. UWP buttons emit
                # synthetic keyboard events on click — they must not become type events)
                ctrl_type = (elem or {}).get("controlType", "")
                if ctrl_type and ctrl_type not in INPUT_CONTROL_TYPES:
                    return
                self._type_elem = elem or {}
            self._type_buffer += char
            return

    def _inspect(self, ins, x, y):
        try:
            elem = ins.element_at(x, y)
            return ins.describe(elem)
        except Exception:
            return {"automationId": "", "className": "", "name": "",
                    "controlType": "", "windowTitle": "", "xpath": "", "hwnd": 0, "pid": 0}

    # ---------------- pending flushes ----------------
    def _flush_stale(self, ins):
        now = time.time()
        if self._pending_click and now - self._pending_click["ts"] > DOUBLE_CLICK_INTERVAL:
            self._flush_pending_click()
        if self._pending_scroll and now - self._pending_scroll["ts"] > SCROLL_FLUSH_IDLE:
            self._flush_pending_scroll()

    def _flush_pending_click(self):
        pc, self._pending_click = self._pending_click, None
        if pc:
            elem = pc["uia_elem"] if pc.get("uia_elem") else pc["elem"]
            self._emit("click", elem, x=pc["x"], y=pc["y"])

    def _flush_pending_scroll(self):
        ps, self._pending_scroll = self._pending_scroll, None
        if ps:
            self._emit("scroll", ps["elem"], x=ps["x"], y=ps["y"],
                       value=str(ps["amount"]))

    def _flush_type_buffer(self):
        text, self._type_buffer = self._type_buffer, ""
        elem, self._type_elem = self._type_elem, None
        if text:
            self._emit("type", elem or {}, value=text)

    def _emit_pointer_event(self, action, x, y, ins):
        self._emit(action, self._inspect(ins, x, y), x=x, y=y)

    # ---------------- emission ----------------
    def _emit(self, action, elem, x=None, y=None, value=None):
        elem = elem or {}
        hwnd = elem.get("hwnd", 0)
        pid = elem.get("pid", 0)
        # application filtering (typing was already filtered at buffer start)
        if action != "type" and not self._belongs_to_target(hwnd, x, y, pid=pid):
            log(f"[skip] {action} hwnd={hwnd} pid={pid} x={x} y={y} — not target app")
            return
        # Cache actual window title for locale-independent _title_matches_app
        wt = elem.get("windowTitle", "")
        if wt and not self.known_window_title:
            self.known_window_title = wt

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
            },
            "timestamp": time.time(),
            "app": self.session.get("appName", ""),
            "platform": self.session.get("platform", "Windows"),
        }
        if value is not None:
            event["value"] = value
        if x is not None:
            event["x"], event["y"] = int(x), int(y)

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
# UIA event thread (dedicated STA + PumpMessages for Invoke events)
# ----------------------------------------------------------------------------
class UIAEventThread:
    """Runs a dedicated STA COM thread with pythoncom.PumpMessages so that
    UIA Invoke events are delivered without a message pump on the worker thread.
    Events are posted to raw_queue as {kind: uia_invoke, elem: ..., ts: ...}.
    self._handler is kept alive to prevent COMObject GC."""

    def __init__(self, raw_queue):
        self._queue = raw_queue
        self._thread_id = 0
        self._ready = threading.Event()
        self._thread = None
        self._handler = None  # GC anchor for the COMObject

    def start(self, target_hwnd):
        self._target_hwnd = target_hwnd
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self._ready.wait(timeout=5.0)

    def _run(self):
        import pythoncom
        import comtypes.client

        pythoncom.CoInitialize()
        self._thread_id = ctypes.windll.kernel32.GetCurrentThreadId()
        registered = False

        try:
            uia = comtypes.client.CreateObject(
                "{ff48dba4-60ef-4201-aa87-54103eef594e}",
                interface=comtypes.client.GetModule(
                    "UIAutomationCore.dll").IUIAutomation,
            )
            if self._target_hwnd:
                elem = uia.ElementFromHandle(self._target_hwnd)
                # _make_invoke_handler lazy-imports IUIAutomationEventHandler;
                # GetModule above ensures comtypes.gen module already exists.
                self._handler = _make_invoke_handler(self._queue)
                uia.AddAutomationEventHandler(
                    UIA_Invoke_InvokedEventId,
                    elem, TreeScope_Subtree, None, self._handler,
                )
                log(f"[uia-events] invoke handler registered hwnd={self._target_hwnd}")
                registered = True
            self._uia = uia
        except Exception:
            log("[uia-events] registration failed — pynput path is fallback")
            traceback.print_exc()
        finally:
            self._ready.set()

        if not registered:
            return  # no message loop needed; pynput handles everything

        pythoncom.PumpMessages()  # blocks; UIA Invoke events delivered on this thread

        try:
            if hasattr(self, '_uia'):
                self._uia.RemoveAllEventHandlers()
        except Exception:
            pass

    def stop(self):
        if self._thread_id:
            # WM_QUIT = 0x0012 breaks PumpMessages
            ctypes.windll.user32.PostThreadMessageW(
                self._thread_id, 0x0012, 0, 0)


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
