"""P-2: Calculator 재생이 STEP 19에서 멈췄을 때 "앱이 죽었나, WAD가 죽었나" 판정.

2026-07-24 로그: STEP 1~18은 정상, STEP 19부터 모든
POST /session/<sid>/element 가 20초 타임아웃(not-found 아님). 실패한 셀렉터는
STEP 2·14에서 이미 성공한 것과 완전히 동일하므로 셀렉터 문제가 아니다.

이 스크립트는 WinAppDriver를 전혀 거치지 않고 독립 COM UIA 클라이언트로 같은
버튼을 조회한다. 입력은 주입하지 않는다.

  - 즉시 찾힌다  → 앱은 멀쩡하다. 죽은 것은 WAD 세션이다.
  - 못 찾는다/느리다 → 앱의 UIA 트리 자체가 이상해진 것이다.

사용법:
  1. 터미널 A: cd generated-wdio/Calculator; node .\\CalculatorTestByClass.js
  2. STEP 19에서 멈추면(20초 타임아웃 메시지가 뜨기 시작하면)
     터미널 B에서: python poc/diag_calc_alive.py
"""
import ctypes
import sys
import time
from ctypes import wintypes

import comtypes
import comtypes.client

UIA_AutomationIdProperty = 30011
UIA_NameProperty = 30005
TreeScope_Subtree = 7

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


def main():
    comtypes.CoInitialize()
    mod = comtypes.client.GetModule("UIAutomationCore.dll")
    uia = comtypes.client.CreateObject(
        "{ff48dba4-60ef-4201-aa87-54103eef594e}", interface=mod.IUIAutomation)

    calc = None
    for h in top_windows():
        buf = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(h, buf, 512)
        if buf.value.strip() in ("계산기", "Calculator"):
            calc = h
            print(f"[diag] Calculator hwnd={h} title='{buf.value}' "
                  f"rect={_rect(h)} responding={_responding(h)}")
            break
    if not calc:
        print("[diag] 계산기 창을 못 찾음 — 재생 중인지 확인하세요.")
        return 2

    root = uia.ElementFromHandle(calc)

    for label, prop, value in (
        ("AutomationId num8Button", UIA_AutomationIdProperty, "num8Button"),
        ("Name '8'", UIA_NameProperty, "8"),
        ("AutomationId CalculatorResults", UIA_AutomationIdProperty, "CalculatorResults"),
    ):
        cond = uia.CreatePropertyCondition(prop, value)
        t0 = time.time()
        try:
            el = root.FindFirst(TreeScope_Subtree, cond)
        except Exception as e:
            print(f"[diag] {label}: EXCEPTION after {(time.time()-t0)*1000:.0f} ms — {e}")
            continue
        dt = (time.time() - t0) * 1000
        if el:
            try:
                r = el.CurrentBoundingRectangle
                extra = f" name='{el.CurrentName}' rect=({r.left},{r.top},{r.right},{r.bottom}) offscreen={el.CurrentIsOffscreen}"
            except Exception as e:
                extra = f" <props unreadable: {e}>"
            print(f"[diag] {label}: FOUND in {dt:.0f} ms{extra}")
        else:
            print(f"[diag] {label}: not found ({dt:.0f} ms)")

    # 표시 중인 값 — 오버플로/에러 상태였는지 확인용
    try:
        cond = uia.CreatePropertyCondition(UIA_AutomationIdProperty, "CalculatorResults")
        disp = root.FindFirst(TreeScope_Subtree, cond)
        if disp:
            print(f"[diag] display text: {disp.CurrentName!r}")
    except Exception as e:
        print(f"[diag] display read failed: {e}")
    return 0


def _rect(h):
    r = wintypes.RECT()
    user32.GetWindowRect(h, ctypes.byref(r))
    return (r.left, r.top, r.right, r.bottom)


def _responding(h, timeout_ms=1000):
    """SendMessageTimeout(WM_NULL) — 창이 메시지 펌프를 돌리고 있는지."""
    res = ctypes.c_ulong()
    ok = user32.SendMessageTimeoutW(h, 0, 0, 0, 0x2, timeout_ms, ctypes.byref(res))
    return bool(ok)


if __name__ == "__main__":
    sys.exit(main())
