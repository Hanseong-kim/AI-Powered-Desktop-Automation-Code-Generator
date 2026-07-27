"""P-1: FileZilla Site Manager 인라인 이름변경 상자 정체 확인 (읽기 전용).

2026-07-24 재생 실패 진단용. `osScopedType`이
sel={"automationId":"1"}로 찾지 못한 이름변경 Edit이
  (a) 아예 다른 automationId/controlType으로 노출되는지
  (b) 단지 늦게 나타나는지 (COM 경로의 재시도 예산 문제)
를 가른다. 입력을 주입하지 않는다 — 오직 조회만 한다.

사용법 (관리자 권한 불필요):
  1. FileZilla를 띄우고 파일(F) > 사이트 관리자(S)를 연다.
  2. 이 스크립트를 먼저 실행한다 (3초간 0.1초 간격으로 폴링 시작).
  3. 곧바로 사이트 관리자에서 "새 사이트(N)" 버튼을 누른다.
  4. 출력에서 새로 나타난 Edit 요소의 automationId/controlType/부모와
     "등장까지 걸린 시간"을 확인한다.
"""
import ctypes
import sys
import time
from ctypes import wintypes

import comtypes
import comtypes.client

UIA_NameProperty = 30005
UIA_AutomationIdProperty = 30011
UIA_ClassNameProperty = 30012
UIA_ControlTypeProperty = 30003
TreeScope_Subtree = 7
CTRL_EDIT = 50004

user32 = ctypes.windll.user32


def top_windows():
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            found.append(hwnd)
        return True

    user32.EnumWindows(cb, 0)
    return found


def describe(uia, el, walker):
    try:
        aid = el.CurrentAutomationId
        name = el.CurrentName
        cls = el.CurrentClassName
        ct = el.CurrentControlType
        r = el.CurrentBoundingRectangle
        rect = (r.left, r.top, r.right, r.bottom)
    except Exception as e:
        return f"<unreadable: {e}>"
    parent = ""
    try:
        p = walker.GetParentElement(el)
        if p:
            parent = f" parent=(id='{p.CurrentAutomationId}' class='{p.CurrentClassName}' ct={p.CurrentControlType})"
    except Exception:
        pass
    return (f"id='{aid}' name='{name}' class='{cls}' controlType={ct} "
            f"rect={rect}{parent}")


def main():
    comtypes.CoInitialize()
    mod = comtypes.client.GetModule("UIAutomationCore.dll")
    uia = comtypes.client.CreateObject(
        "{ff48dba4-60ef-4201-aa87-54103eef594e}", interface=mod.IUIAutomation)
    walker = uia.RawViewWalker

    dlg = None
    for h in top_windows():
        try:
            title = ctypes.create_unicode_buffer(512)
            user32.GetWindowTextW(h, title, 512)
            if "사이트 관리자" in title.value or "Site Manager" in title.value:
                dlg = h
                print(f"[diag] Site Manager dialog hwnd={h} title='{title.value}'")
                break
        except Exception:
            continue
    if not dlg:
        print("[diag] 사이트 관리자 다이얼로그를 못 찾음 — 먼저 열어두고 다시 실행하세요.")
        return 2

    root = uia.ElementFromHandle(dlg)
    cond_edit = uia.CreatePropertyCondition(UIA_ControlTypeProperty, CTRL_EDIT)

    def snapshot():
        out = {}
        try:
            arr = root.FindAll(TreeScope_Subtree, cond_edit)
        except Exception:
            return out
        for i in range(arr.Length):
            try:
                el = arr.GetElement(i)
                r = el.CurrentBoundingRectangle
                out[(el.CurrentAutomationId, r.left, r.top)] = el
            except Exception:
                pass
        return out

    base = snapshot()
    print(f"[diag] baseline Edit controls: {len(base)}")
    for el in base.values():
        print("   ", describe(uia, el, walker))

    print("\n[diag] 지금 '새 사이트(N)'를 누르세요 — 3초간 0.1초 간격으로 감시합니다.")
    t0 = time.time()
    seen = False
    while time.time() - t0 < 3.0:
        cur = snapshot()
        new = [k for k in cur if k not in base]
        if new:
            dt = time.time() - t0
            print(f"\n[diag] NEW Edit control(s) after {dt*1000:.0f} ms:")
            for k in new:
                print("   ", describe(uia, cur[k], walker))
            seen = True
            break
        time.sleep(0.1)

    if not seen:
        print("\n[diag] 3초 안에 새 Edit이 안 나타났습니다 — 이름변경 상자가 Edit이 "
              "아닌 다른 controlType으로 노출되거나, 별도 창에 있을 수 있습니다.")
        print("[diag] 다이얼로그 서브트리의 현재 Edit 목록:")
        for el in snapshot().values():
            print("   ", describe(uia, el, walker))
    return 0


if __name__ == "__main__":
    sys.exit(main())
