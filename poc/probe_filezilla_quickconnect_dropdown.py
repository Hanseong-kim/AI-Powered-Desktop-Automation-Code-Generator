"""
================================================================================
  FileZilla Quick-Connect "Clear" Dropdown — Container Shape Probe
================================================================================

WHAT THIS IS FOR

    agent.py's snapshot_open_menu() only recognizes a popup container whose
    own UIA ControlType is exactly Menu (50009) as something worth
    snapshotting while it's open. Recording the FileZilla Quick-Connect bar's
    small ▼ dropdown and picking "빠른 연결 입력란 지우기" (Clear quick-connect
    bar) produced this log line:

        snapshot_open_menu: '...' was Expanded but no Menu container with
        items was found

    ...meaning the item's menuItemIndex/menuItemCount never got populated,
    which is why replay could not find it later (see the 2026-08-08 session
    notes / plan file for the full chain of reasoning). Before writing the
    code that broadens snapshot_open_menu()'s container-detection condition,
    we need to know what this dropdown's ACTUAL container shape is — this
    script finds the item live and walks its ancestor chain, printing
    ControlType/ClassName/AutomationId/Name at every level up to the window
    root, so the exact condition to relax can be chosen instead of guessed.

    This performs ONE real UI action by default: it finds the ▼ trigger
    button (by its stable Win32 AutomationId) and opens the dropdown. It
    tries structural pattern calls FIRST (ExpandCollapsePattern.Expand(),
    then InvokePattern.Invoke()) since those need no coordinates — but
    measured live (2026-08-08) this control answers BOTH of those as
    unsupported/no-op: it is a custom-drawn wx button that only reacts to a
    real mouse click, exactly the class of control CLAUDE.md §3 already
    carves out an exception for ("a dynamic ClickablePoint + SendInput
    computed at replay time from a freshly resolved element ... allowed
    only when patterns don't work"). So when both patterns fail, this
    script falls back to send_input_click() — a trimmed port of
    server.js's own COM_INPUT_PY helper of the same name: reads the
    element's live ClickablePoint (never a stored/typed-in coordinate),
    converts to normalized absolute SendInput coordinates, and does a real
    move+down+up. Pass --no-auto-open to skip all of this and only search
    for whatever is already open.

    Why auto-open at all: opening the dropdown by hand and then switching
    focus to a terminal to run this script closes the dropdown BEFORE the
    script runs (measured 2026-08-08 — Alt-Tab away from FileZilla dismisses
    it). Doing the open programmatically, in the same process, right before
    the search, avoids that focus-switch race entirely.


HOW TO RUN IT

    1. Launch FileZilla (it can be the active window or not — no manual
       clicking needed).
    2. Run, from any terminal:

           python poc/probe_filezilla_quickconnect_dropdown.py

       (Admin PowerShell recommended — matches this project's usual
       AutomationId/Name visibility requirement, see CLAUDE.md §5.)

    The script finds the ▼ trigger itself, expands it, and immediately
    searches for the item — no need to hover/click anything or keep a
    window in the foreground.

    Optional: --name lets you probe a different item by its exact UIA Name
    (default is the item captured during this session's recording, including
    its recorded — possibly UIA-reported-as-is — spelling). --title lets you
    match a different top-level window title fragment than "FileZilla".
    --trigger-automation-id overrides the ▼ button's AutomationId if it
    turns out to differ from "-31944" (the value captured live this
    session). --no-auto-open falls back to the original manual-open flow
    (open it yourself, run this within a couple seconds, accept the risk
    that switching windows may already have closed it).

WHAT IT PRINTS

    - Every top-level window belonging to the FileZilla process, with its
      hwnd/ClassName/ControlType/Name — so you can see whether the dropdown
      rendered as a NEW top-level window (like FileZilla's menu bar does) or
      stayed inside the main window's own subtree.
    - If the target item is found: the full ancestor chain from the item up
      to whichever root it was found under, one line per level, each showing
      ControlType (name + numeric id), ClassName, AutomationId, Name, hwnd.
    - If NOT found: says so plainly. Likely means the dropdown had already
      closed by the time the probe ran (it may be short-lived) — rerun with
      the dropdown freshly opened and this script started immediately after.
"""

import argparse
import ctypes
import sys
import time
from ctypes import wintypes

if sys.platform != "win32":
    print("Windows only.")
    sys.exit(1)

import comtypes
import comtypes.client

DEFAULT_ITEM_NAME = "빠른 연결 입력란 자우기"  # exact string captured live this session (see plan file)

# ── SendInput fallback (trimmed port of server.js's COM_INPUT_PY send_input_click) ──
SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN = 76, 77
SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 78, 79
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_VIRTUALDESK = 0x4000
MOUSEEVENTF_ABSOLUTE = 0x8000
INPUT_MOUSE = 0
ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


def enable_per_monitor_dpi():
    # 125% 스케일 환경에서 UIA rect/ClickablePoint(논리 좌표)와 SendInput(물리
    # 픽셀)의 좌표계가 어긋나는 것을 막는다 — UIA 객체 생성 전에 호출 필수.
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        return
    except Exception:
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass


def clickable_point(el):
    try:
        res = el.GetClickablePoint()
    except Exception:
        res = None
    if res is not None:
        pt, got = (res if isinstance(res, (tuple, list)) and len(res) == 2 else (res, 1))
        if got and pt is not None:
            try:
                return int(pt.x), int(pt.y)
            except Exception:
                pass
    try:
        r = el.CurrentBoundingRectangle
        if r.right > r.left and r.bottom > r.top:
            return (r.left + r.right) // 2, (r.top + r.bottom) // 2
    except Exception:
        pass
    return None


def send_input_click(el):
    """살아있는 el에서 방금 계산한 ClickablePoint로 실제 마우스 클릭 —
    server.js send_input_click()의 축약 버전 (offscreen/pid-at-point 이중
    검증은 생략, 이 probe는 단일 알려진 버튼만 클릭하므로)."""
    pt = clickable_point(el)
    if not pt:
        print("  send_input_click: no clickable point")
        return False
    x, y = pt
    user32 = ctypes.windll.user32
    vx, vy = user32.GetSystemMetrics(SM_XVIRTUALSCREEN), user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
    vw, vh = user32.GetSystemMetrics(SM_CXVIRTUALSCREEN), user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)
    if vw <= 1 or vh <= 1:
        print("  send_input_click: virtual screen metrics unavailable")
        return False
    nx = int(round((x - vx) * 65535.0 / (vw - 1)))
    ny = int(round((y - vy) * 65535.0 / (vh - 1)))

    def send(flags):
        inp = INPUT(type=INPUT_MOUSE)
        inp.mi = MOUSEINPUT(nx, ny, 0, flags, 0, 0)
        return user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))

    send(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK)
    time.sleep(0.04)
    send(MOUSEEVENTF_LEFTDOWN | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK)
    time.sleep(0.04)
    send(MOUSEEVENTF_LEFTUP | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK)
    print(f"  send_input_click: clicked at ({x},{y})")
    return True

UIA_CONTROL_TYPES = {
    50000: "Button", 50002: "Calendar", 50003: "CheckBox", 50004: "ComboBox",
    50005: "Edit", 50006: "Hyperlink", 50007: "Image", 50008: "ListItem",
    50009: "Menu", 50010: "MenuBar", 50011: "MenuItem", 50012: "ProgressBar",
    50013: "RadioButton", 50014: "ScrollBar", 50015: "Slider", 50016: "Spinner",
    50017: "StatusBar", 50018: "Tab", 50019: "TabItem", 50020: "Text",
    50021: "ToolBar", 50022: "ToolTip", 50023: "Tree", 50024: "TreeItem",
    50025: "Custom", 50026: "Group", 50027: "Thumb", 50028: "DataGrid",
    50029: "DataItem", 50030: "Document", 50031: "SplitButton", 50032: "Window",
    50033: "Pane", 50034: "Header", 50035: "HeaderItem", 50036: "Table",
    50037: "TitleBar", 50038: "Separator", 50039: "SemanticZoom", 50040: "AppBar",
}


def ct_name(ct):
    return f"{UIA_CONTROL_TYPES.get(ct, '?')}({ct})"


def describe_line(el):
    try:
        ct = ct_name(el.CurrentControlType)
    except Exception:
        ct = "?"
    try:
        cls = el.CurrentClassName or ""
    except Exception:
        cls = "?"
    try:
        aid = el.CurrentAutomationId or ""
    except Exception:
        aid = "?"
    try:
        name = el.CurrentName or ""
    except Exception:
        name = "?"
    try:
        hwnd = el.CurrentNativeWindowHandle or 0
    except Exception:
        hwnd = 0
    return f"ControlType={ct:<16} ClassName={cls!r:<24} AutomationId={aid!r:<10} Name={name!r} hwnd={hwnd}"


def enum_top_windows():
    hwnds = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, lparam):
        hwnds.append(hwnd)
        return True

    ctypes.windll.user32.EnumWindows(cb, 0)
    return hwnds


def window_pid(hwnd):
    pid = wintypes.DWORD()
    ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def window_title(hwnd):
    length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(length + 1)
    ctypes.windll.user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value


def window_class(hwnd):
    buf = ctypes.create_unicode_buffer(256)
    ctypes.windll.user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--title", default="FileZilla", help="top-level window title fragment to match")
    ap.add_argument("--name", default=DEFAULT_ITEM_NAME, help="exact UIA Name of the dropdown item to locate")
    ap.add_argument("--trigger-automation-id", default="-31944",
                     help="AutomationId of the ▼ button that opens the dropdown (a stable Win32 "
                          "resource id, not the volatile per-session counter on the ITEM inside "
                          "it) — captured live this session, see DEFAULT_ITEM_NAME's sibling clicks")
    ap.add_argument("--no-auto-open", action="store_true",
                     help="skip the Expand() call and just search for --name as-is — use this if "
                          "you already have the dropdown open yourself and switching to this "
                          "terminal hasn't closed it yet (Alt-Tab away from FileZilla usually DOES "
                          "close it, which is exactly what --auto-open avoids)")
    args = ap.parse_args()

    enable_per_monitor_dpi()  # must happen before the UIA object is created
    comtypes.CoInitialize()
    mod = comtypes.client.GetModule("UIAutomationCore.dll")
    uia = comtypes.client.CreateObject(
        "{ff48dba4-60ef-4201-aa87-54103eef594e}", interface=mod.IUIAutomation
    )

    print(f"Looking for a top-level window matching title fragment {args.title!r}...")
    all_visible = [hwnd for hwnd in enum_top_windows() if ctypes.windll.user32.IsWindowVisible(hwnd)]
    title_matched = [hwnd for hwnd in all_visible if args.title.lower() in window_title(hwnd).lower()]
    if not title_matched:
        print(f"No visible top-level window matched {args.title!r}. Is FileZilla running?")
        sys.exit(1)

    # 2026-08-08 실측: 트리거를 누르면 title=''(빈 제목)인 팝업 창이 새로
    # 뜬다(agent.py 캡처 로그의 [watcher] added hwnd=... title='' 이 그 증거).
    # title로만 걸러내면 이 팝업을 통째로 놓친다. agent.py 자신도 title이 아니라
    # PID로 "같은 앱의 다른 최상위 창"을 판별한다 — 여기서도 같은 기준을 쓴다:
    # title이 매칭된 창(들)의 PID를 구해서, 같은 PID를 쓰는 모든 보이는
    # 최상위 창(제목이 비어 있어도)을 전부 검색 대상에 포함시킨다.
    target_pids = {window_pid(hwnd) for hwnd in title_matched}
    matched_hwnds = [hwnd for hwnd in all_visible if window_pid(hwnd) in target_pids]
    print(f"  title match found {len(title_matched)} window(s), pid(s)={sorted(target_pids)} "
          f"-> {len(matched_hwnds)} window(s) total once other top-level windows of the same "
          f"process(es) are included")

    print(f"\n{len(matched_hwnds)} matching top-level window(s):")
    roots = []
    for hwnd in matched_hwnds:
        try:
            el = uia.ElementFromHandle(hwnd)
        except Exception as e:
            print(f"  hwnd={hwnd} (0x{hwnd:x}) title={window_title(hwnd)!r} "
                  f"cls={window_class(hwnd)!r} -- ElementFromHandle failed: {e}")
            continue
        roots.append((hwnd, el))
        print(f"  hwnd={hwnd} (0x{hwnd:x}) title={window_title(hwnd)!r} cls={window_class(hwnd)!r} "
              f"-- {describe_line(el)}")

    if not args.no_auto_open:
        # 사람이 드롭다운을 열어두고 이 터미널로 포커스를 옮기면 그 전환 자체가
        # FileZilla의 드롭다운을 닫아버린다(2026-08-08 실측). 대신 여기서
        # 트리거를 찾아 ExpandCollapsePattern.Expand()로 프로그램적으로 열고,
        # 포커스 전환 없이 바로 같은 프로세스 안에서 검색까지 이어간다 — 좌표
        # 클릭이 아니라 구조적 패턴 호출이므로 §3 "좌표 금지" 원칙과도 맞는다.
        print(f"\nAuto-opening the dropdown via its trigger "
              f"(AutomationId={args.trigger_automation_id!r})...")
        aid_cond = uia.CreatePropertyCondition(30011, args.trigger_automation_id)  # UIA_AutomationIdPropertyId
        trigger = None
        trigger_root_hwnd = None
        for hwnd, root_el in roots:
            try:
                found = root_el.FindFirst(4, aid_cond)  # TreeScope_Descendants
            except Exception:
                continue
            if found:
                trigger = found
                trigger_root_hwnd = hwnd
                break
        if trigger is None:
            print(f"  trigger not found under any matched window (tried AutomationId="
                  f"{args.trigger_automation_id!r}) — pass --trigger-automation-id if it has "
                  f"changed, or --no-auto-open to search as-is")
        else:
            print(f"  trigger found under hwnd={trigger_root_hwnd} -- {describe_line(trigger)}")
            opened = False
            try:
                ecp = trigger.GetCurrentPattern(10005)  # UIA_ExpandCollapsePatternId
                if ecp:
                    ecp.QueryInterface(mod.IUIAutomationExpandCollapsePattern).Expand()
                    print("  Expand() succeeded")
                    opened = True
            except Exception:
                pass
            if not opened:
                try:
                    inv = trigger.GetCurrentPattern(10000)  # UIA_InvokePatternId
                    if inv:
                        inv.QueryInterface(mod.IUIAutomationInvokePattern).Invoke()
                        print("  ExpandCollapsePattern unsupported — Invoke() succeeded")
                        opened = True
                except Exception:
                    pass
            if not opened:
                # 2026-08-08 실측: 이 버튼은 Expand()도 Invoke()도 조용히
                # 아무 일도 안 한다(패턴은 있는 척하거나 없다고 답하지만 실제
                # 핸들러가 없는 wx 커스텀 버튼) — 실제 녹화에서 드롭다운을 연 건
                # 사람의 진짜 마우스 클릭뿐이었다. CLAUDE.md §3이 이 경우를 위해
                # 허용한 "재생 시점에 방금 계산한 ClickablePoint + SendInput"으로
                # 폴백한다.
                print("  ExpandCollapsePattern and InvokePattern both unavailable/no-op — "
                      "falling back to a real SendInput click (CLAUDE.md §3's documented "
                      "escape hatch for exactly this case)")
                opened = send_input_click(trigger)
            if opened:
                print("  waiting briefly for the popup to render...")
                time.sleep(0.4)
                # 클릭 이후에 새로 뜬 최상위 창(제목 없는 팝업 등)을 포함하도록
                # 검색 대상 창 목록을 다시 스캔한다 — 클릭 전에 만든 roots에는
                # 아직 없을 수 있다.
                all_visible = [h for h in enum_top_windows() if ctypes.windll.user32.IsWindowVisible(h)]
                fresh_hwnds = [h for h in all_visible if window_pid(h) in target_pids]
                new_hwnds = [h for h in fresh_hwnds if h not in matched_hwnds]
                if new_hwnds:
                    print(f"  {len(new_hwnds)} new top-level window(s) appeared after opening: "
                          f"{[hex(h) for h in new_hwnds]}")
                    for h in new_hwnds:
                        try:
                            el = uia.ElementFromHandle(h)
                        except Exception as e:
                            print(f"    hwnd={h} (0x{h:x}) title={window_title(h)!r} "
                                  f"cls={window_class(h)!r} -- ElementFromHandle failed: {e}")
                            continue
                        roots.append((h, el))
                        matched_hwnds.append(h)
                        print(f"    hwnd={h} (0x{h:x}) title={window_title(h)!r} cls={window_class(h)!r} "
                              f"-- {describe_line(el)}")
                else:
                    print("  no new top-level windows appeared — the item must be somewhere in "
                          "the windows already listed above")

    print(f"\nSearching for item with Name={args.name!r} under each matched window's FULL subtree...")
    target = None
    target_root = None
    name_cond = uia.CreatePropertyCondition(30005, args.name)  # UIA_NamePropertyId
    for hwnd, root_el in roots:
        try:
            found = root_el.FindFirst(4, name_cond)  # TreeScope_Descendants
        except Exception as e:
            print(f"  (search under hwnd={hwnd} failed: {e})")
            continue
        if found:
            target = found
            target_root = (hwnd, root_el)
            print(f"  FOUND under hwnd={hwnd} (0x{hwnd:x})")
            break

    if target is None:
        print("\nNOT FOUND. Likely causes:")
        print("  - the dropdown already closed before this probe ran (it may be short-lived)")
        print("  - the item's live Name differs from --name (try without --name to use the default,")
        print("    or pass the exact string from this session's capture log)")
        print("  - the item lives in a NEW top-level window that didn't match --title")
        print("    (rerun with a broader --title, or check the full EnumWindows list above)")
        sys.exit(1)

    print(f"\nAncestor chain for the found item, item first, walking up to the window root:")
    print(f"  [item]   {describe_line(target)}")
    walker = uia.ControlViewWalker
    cur = target
    depth = 0
    while depth < 12:
        try:
            parent = walker.GetParentElement(cur)
        except Exception as e:
            print(f"  (GetParentElement failed at depth {depth}: {e})")
            break
        if parent is None:
            break
        depth += 1
        print(f"  [up {depth}] {describe_line(parent)}")
        try:
            if parent.CurrentNativeWindowHandle and parent.CurrentNativeWindowHandle == target_root[0]:
                break
        except Exception:
            pass
        cur = parent

    print("\nDone. Use the container ControlType(s) shown above (the level(s) between [item] and the")
    print("window root) to decide snapshot_open_menu()'s broadened container-detection condition —")
    print("see the plan file's B.3 section for where this feeds back into agent.py.")


if __name__ == "__main__":
    main()
