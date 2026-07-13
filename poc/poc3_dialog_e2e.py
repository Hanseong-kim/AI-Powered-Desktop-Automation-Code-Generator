"""PoC 3 E2E — 보조 창(속성 다이얼로그) 열기 → 고유 HWND 캡처 → 그 창 컨텍스트
안에서만 클릭 격리 (2026-07-12, 07-10 미완주분 완주).

대상: Windows 탐색기 (Explorer — 항상 비승격, 현대적 UIA 노출; PoC 2에서
      ScrollPattern 실측에 사용한 것과 같은 앱)
스택: COM IUIAutomation (comtypes) — 프로덕션 agent.py와 동일한 UIA 스택.

흐름:
  1. 탐색기로 이 저장소의 poc/ 폴더 열기 → CabinetWClass hwnd 확보
     → 최상위 hwnd 베이스라인 스냅샷
  2. 파일 목록에서 FINDINGS.md ListItem을 UIA로 찾아 SetFocus +
     SelectionItemPattern.Select() (요소 기반 선택 — 좌표 0회)
  3. Alt+Enter(키보드 가속기)로 속성 다이얼로그 오픈
  4. EnumWindows 차분으로 새 최상위 #32770 hwnd 캡처
  5. 격리 증명: '취소' 버튼 쿼리를 탐색기 창 서브트리에 스코프 → 미발견,
     캡처한 다이얼로그 hwnd 서브트리에 스코프 → 발견 → UIA Invoke(요소
     클릭) → 다이얼로그 닫힘 확인
  6. 정리: 탐색기 창을 PostMessage(WM_CLOSE)로 종료

전 과정에서 SetCursorPos / mouse_event / 픽셀 좌표 사용 0회.

시도했다가 배제한 대상 (2026-07-12 실측, 제출문서에 기록):
  - services.msc(MMC): 이 머신에서 highestAvailable 매니페스트로 승격 실행됨
    → 비승격 스크립트에서 UIPI가 UIA 자식 조회/키 주입 모두 차단 (PoC 1의
    regedit 트랩과 동일). 또한 가상(LVS_OWNERDATA) SysListView32는 승격을
    떠나 UIA 행 아이템을 노출하지 않음.

실행: python poc/poc3_dialog_e2e.py   (admin 불필요)
"""
import ctypes
import os
import sys
import time
from ctypes import wintypes

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import comtypes
import comtypes.client

user32 = ctypes.windll.user32

# UIA property / pattern / control-type IDs
UIA_ControlTypeProperty = 30003
UIA_NameProperty = 30005
UIA_InvokePatternId = 10000
UIA_SelectionItemPatternId = 10010
CT_Button = 50000
CT_ListItem = 50007
TreeScope_Descendants = 4
WM_CLOSE = 0x0010
VK_MENU, VK_RETURN, KEYEVENTF_KEYUP = 0x12, 0x0D, 0x0002

POC_DIR = os.path.dirname(os.path.abspath(__file__))
TARGET_FILE = "FINDINGS.md"


def top_windows(pid=None):
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            if pid is None:
                found.append(hwnd)
            else:
                wpid = wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(wpid))
                if wpid.value == pid:
                    found.append(hwnd)
        return True

    user32.EnumWindows(cb, 0)
    return found


def win_class(hwnd):
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def win_title(hwnd):
    buf = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(hwnd, buf, 512)
    return buf.value


def main():
    comtypes.CoInitialize()
    mod = comtypes.client.GetModule("UIAutomationCore.dll")
    uia = comtypes.client.CreateObject(
        "{ff48dba4-60ef-4201-aa87-54103eef594e}", interface=mod.IUIAutomation
    )

    # 1. open Explorer at the poc folder + baseline -------------------------
    # ShellExecute on a folder REUSES an existing Explorer window showing that
    # folder — locate it via Shell.Application (matching the folder path)
    # instead of relying on a new-hwnd diff.
    poc_url = "file:///" + POC_DIR.replace("\\", "/").lower()

    def find_poc_explorer():
        try:
            shell = comtypes.client.CreateObject("Shell.Application")
            ws = shell.Windows()
            for i in range(ws.Count):
                try:
                    w = ws.Item(i)
                    if w and str(w.LocationURL).lower() == poc_url:
                        return int(w.HWND)
                except Exception:
                    continue
        except Exception:
            pass
        return None

    print(f"[1] opening Explorer at {POC_DIR}")
    exp_hwnd = find_poc_explorer()
    if exp_hwnd is None:
        ctypes.windll.shell32.ShellExecuteW(None, "open", POC_DIR, None, None, 1)
        deadline = time.time() + 15
        while time.time() < deadline and exp_hwnd is None:
            time.sleep(0.5)
            exp_hwnd = find_poc_explorer()
    if exp_hwnd is None:
        sys.exit("FAIL: Explorer window did not appear")
    print(f"[1] explorer hwnd={exp_hwnd:#x} class=CabinetWClass "
          f"title={win_title(exp_hwnd)!r}")
    time.sleep(1.5)  # let the item view populate
    baseline = set(top_windows())
    print(f"[1] baseline top-level windows: {len(baseline)}")

    exp_el = uia.ElementFromHandle(exp_hwnd)
    if not exp_el:
        sys.exit("FAIL: ElementFromHandle failed for Explorer window")

    # 2. select the target file via UIA (element-based, no coordinates) -----
    # Explorer may hide file extensions, so the ListItem Name can be
    # 'FINDINGS' rather than 'FINDINGS.md' — enumerate and prefix-match.
    stem = TARGET_FILE.rsplit(".", 1)[0]
    li_cond = uia.CreatePropertyCondition(UIA_ControlTypeProperty, CT_ListItem)
    item = None
    deadline = time.time() + 15
    while time.time() < deadline and not item:
        items = exp_el.FindAll(TreeScope_Descendants, li_cond)
        names = []
        for i in range(items.Length):
            el = items.GetElement(i)
            names.append(el.CurrentName)
            if el.CurrentName.startswith(stem):
                item = el
                break
        if not item:
            time.sleep(0.5)
    if not item:
        sys.exit(f"FAIL: no ListItem starting with {stem!r} in Explorer view "
                 f"(saw: {names[:10]})")
    print(f"[2] found ListItem {item.CurrentName!r} — "
          "SetFocus + SelectionItemPattern.Select() (no coords)")
    item.SetFocus()
    item.GetCurrentPattern(UIA_SelectionItemPatternId).QueryInterface(
        mod.IUIAutomationSelectionItemPattern
    ).Select()
    time.sleep(0.5)

    # 3. open the Properties dialog (Alt+Enter accelerator) ------------------
    print("[3] opening Properties via Alt+Enter (keyboard — no coords)")
    user32.SetForegroundWindow(exp_hwnd)
    time.sleep(0.3)
    user32.keybd_event(VK_MENU, 0, 0, 0)
    user32.keybd_event(VK_RETURN, 0, 0, 0)
    user32.keybd_event(VK_RETURN, 0, KEYEVENTF_KEYUP, 0)
    user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)

    # 4. capture the NEW top-level hwnd (EnumWindows diff) -------------------
    dlg_hwnd = None
    deadline = time.time() + 10
    while time.time() < deadline and dlg_hwnd is None:
        time.sleep(0.3)
        for h in top_windows():
            if h not in baseline and win_class(h) == "#32770":
                dlg_hwnd = h
                break
    if dlg_hwnd is None:
        sys.exit("FAIL: no new #32770 dialog hwnd appeared")
    print(f"[4] NEW dialog hwnd={dlg_hwnd:#x} class=#32770 "
          f"title={win_title(dlg_hwnd)!r}")

    # 5. isolation proof ------------------------------------------------------
    cancel_cond = uia.CreateAndCondition(
        uia.CreatePropertyCondition(UIA_ControlTypeProperty, CT_Button),
        uia.CreatePropertyCondition(UIA_NameProperty, "취소"),
    )
    in_main = exp_el.FindFirst(TreeScope_Descendants, cancel_cond)
    print("[5a] '취소' scoped to the EXPLORER window subtree: "
          + ("FOUND (unexpected!)" if in_main else "not found — isolation holds"))

    dlg_el = uia.ElementFromHandle(dlg_hwnd)
    cancel = dlg_el.FindFirst(TreeScope_Descendants, cancel_cond)
    if not cancel:
        sys.exit("FAIL: '취소' not found inside dialog subtree")
    print("[5b] '취소' resolved INSIDE the captured dialog subtree — "
          "invoking via UIA InvokePattern (element click, no coords)")
    cancel.GetCurrentPattern(UIA_InvokePatternId).QueryInterface(
        mod.IUIAutomationInvokePattern
    ).Invoke()
    time.sleep(0.8)
    closed = dlg_hwnd not in top_windows()
    print(f"[5b] dialog closed after scoped click: {'YES' if closed else 'NO (still open)'}")

    # 6. cleanup ---------------------------------------------------------------
    user32.PostMessageW(exp_hwnd, WM_CLOSE, 0, 0)
    print("[6] Explorer window closed via PostMessage(WM_CLOSE)")
    print()
    print("PoC3-E2E RESULT: secondary window (file Properties dialog) opened from a")
    print("UIA-selected element, its unique HWND captured via EnumWindows diff, and")
    print("the follow-up click resolved and executed strictly inside that HWND's UIA")
    print("subtree. Zero pixel APIs used (no SetCursorPos / mouse_event / coords).")


if __name__ == "__main__":
    main()
