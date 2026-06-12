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

GA_ROOT = 2  # GetAncestor flag


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
        return self._uia.ElementFromPoint(pt)

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
                info["windowTitle"] = win32gui.GetWindowText(root or hwnd)
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
            if p.name().lower() == self.target_exe:
                self.target_pids.add(pid)
                return True
            # accept descendants of the launched process (some apps re-spawn)
            if self.proc and self.proc.pid:
                for anc in p.parents():
                    if anc.pid == self.proc.pid:
                        self.target_pids.add(pid)
                        return True
        except Exception:
            pass
        return False

    def _belongs_to_target(self, hwnd, x=None, y=None):
        if not hwnd and x is not None:
            hwnd = hwnd_at_point(x, y)
        return self._is_target_pid(pid_of_hwnd(hwnd))

    # ---------------- worker (UIA lookups + emission happen here) ----------
    def _worker_loop(self):
        try:
            inspector = UIAInspector()
        except Exception:
            log("FATAL: could not initialise UI Automation")
            traceback.print_exc()
            return

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
                                       "elem": self._inspect(ins, x, y)}
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

            if special in ("tab", "enter"):
                self._flush_type_buffer()
                return
            if special == "backspace":
                self._type_buffer = self._type_buffer[:-1]
                return
            if special == "space":
                char = " "
            if char is None:
                return  # shift/ctrl/arrows etc. - ignored

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
                self._type_elem = elem or {}
            self._type_buffer += char
            return

    def _inspect(self, ins, x, y):
        try:
            elem = ins.element_at(x, y)
            return ins.describe(elem)
        except Exception:
            return {"automationId": "", "className": "", "name": "",
                    "controlType": "", "windowTitle": "", "xpath": "", "hwnd": 0}

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
            self._emit("click", pc["elem"], x=pc["x"], y=pc["y"])

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
        # application filtering (typing was already filtered at buffer start)
        if action != "type" and not self._belongs_to_target(hwnd, x, y):
            return

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
