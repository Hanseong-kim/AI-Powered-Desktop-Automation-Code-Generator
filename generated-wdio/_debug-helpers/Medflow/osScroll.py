import sys, json, base64, argparse, ctypes
from ctypes import wintypes

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if sys.stderr.encoding and sys.stderr.encoding.lower() not in ("utf-8", "utf8"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import comtypes
import comtypes.client

UIA_NameProperty = 30005
UIA_AutomationIdProperty = 30011
UIA_ClassNameProperty = 30012
UIA_ScrollPatternId = 10004
TreeScope_Descendants = 4
WM_MOUSEWHEEL = 0x020A
# ScrollAmount enum (UIAutomationClient.h): LargeDecrement=0, SmallDecrement=1,
# NoAmount=2, LargeIncrement=3, SmallIncrement=4.
SCROLL_NO_AMOUNT = 2
SCROLL_SMALL_DECREMENT = 1
SCROLL_SMALL_INCREMENT = 4

user32 = ctypes.windll.user32
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, ctypes.c_size_t, ctypes.c_ssize_t]
user32.PostMessageW.restype = wintypes.BOOL


def find_target(uia, root, sel):
    # 캡처 시점에 agent.py가 ScrollPattern 보유 조상으로 걸어 올라가 기록한
    # 컨테이너 셀렉터 — PS1과 동일하게 automationId/className/name 순으로
    # 단독 조건을 하나씩 시도(AND 아님).
    if sel:
        for prop, key in ((UIA_AutomationIdProperty, "automationId"),
                           (UIA_ClassNameProperty, "className"),
                           (UIA_NameProperty, "name")):
            if sel.get(key):
                try:
                    cond = uia.CreatePropertyCondition(prop, sel[key])
                    t = root.FindFirst(TreeScope_Descendants, cond)
                    if t:
                        return t
                except Exception:
                    pass
    return root


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hwnd", type=int, required=True)
    ap.add_argument("--sel-b64", default="")
    ap.add_argument("--delta", type=int, required=True)
    args = ap.parse_args()

    if not args.hwnd:
        print("osScroll: --hwnd is required", file=sys.stderr)
        sys.exit(2)

    comtypes.CoInitialize()
    mod = comtypes.client.GetModule("UIAutomationCore.dll")
    uia = comtypes.client.CreateObject(
        "{ff48dba4-60ef-4201-aa87-54103eef594e}", interface=mod.IUIAutomation
    )

    try:
        root = uia.ElementFromHandle(args.hwnd)
    except Exception as e:
        print(f"osScroll: ElementFromHandle raised: {e}", file=sys.stderr)
        sys.exit(2)
    if not root:
        print("osScroll: ElementFromHandle failed", file=sys.stderr)
        sys.exit(2)

    sel = None
    if args.sel_b64:
        try:
            sel = json.loads(base64.b64decode(args.sel_b64).decode("utf-8"))
        except Exception:
            sel = None
    target = find_target(uia, root, sel)

    # 1차: 대상(또는 가장 가까운 스크롤 가능 조상)의 ScrollPattern.
    walker = uia.ControlViewWalker
    cur = target
    scroll = None
    for _ in range(10):
        if cur is None:
            break
        try:
            p = cur.GetCurrentPattern(UIA_ScrollPatternId)
            if p:
                sp = p.QueryInterface(mod.IUIAutomationScrollPattern)
                if sp.CurrentVerticallyScrollable:
                    scroll = sp
                    break
        except Exception:
            pass
        try:
            cur = walker.GetParentElement(cur)
        except Exception:
            break

    if scroll:
        try:
            before = scroll.CurrentVerticalScrollPercent
        except Exception:
            before = None
        # 휠 업(양수 delta) = 콘텐츠 위로 = SmallDecrement. 노치당 약 3줄.
        direction = SCROLL_SMALL_DECREMENT if args.delta > 0 else SCROLL_SMALL_INCREMENT
        n = abs(args.delta) * 3
        # Scroll()은 PS1 원본에도 예외처리가 없던 자리 — 콤보 팝업이 스크롤
        # 도중 상태를 바꾸면(자동 닫힘 등) 반복 호출 중 COM 예외를 던져 스크립트
        # 전체가 죽고, 그게 _step()의 ESC 복구로 이어져 다이얼로그 기반 앱
        # (ESC==Cancel)을 통째로 닫혀버리게 만드는 것을 실측(2026-07-14, PuTTY
        # STEP 6). 1회라도 성공했으면 성공으로 보고하고, 중간에 실패하면 그
        # 지점에서 멈추고 아래로 흘려보낸다 — 한 번도 못 돌렸으면(scrolled==0)
        # ScrollPattern 자체가 못 미더운 것으로 보고 PostMessageW 폴백으로.
        scrolled = 0
        for _ in range(n):
            try:
                scroll.Scroll(SCROLL_NO_AMOUNT, direction)
                scrolled += 1
            except Exception as e:
                print(f"[osScroll] WARN Scroll() failed after {scrolled}/{n} notches: {e}")
                break
        if scrolled > 0:
            try:
                after = scroll.CurrentVerticalScrollPercent
            except Exception:
                after = None
            print(f"[osScroll] ScrollPattern {before} -> {after} (delta={args.delta}, {scrolled}/{n} notches applied)")
            sys.exit(0)
        print("[osScroll] WARN ScrollPattern found but Scroll() failed immediately — falling back to PostMessageW")

    # 2차: hwnd-scoped WM_MOUSEWHEEL (PostMessageW — 비동기, SendMessage 금지).
    post_h = args.hwnd
    cur = target
    for _ in range(10):
        if cur is None:
            break
        try:
            nh = cur.CurrentNativeWindowHandle
            if nh:
                post_h = nh
                break
            cur = walker.GetParentElement(cur)
        except Exception:
            break

    cx = cy = 0
    try:
        r = target.CurrentBoundingRectangle
        cx = int(r.left + (r.right - r.left) / 2)
        cy = int(r.top + (r.bottom - r.top) / 2)
    except Exception:
        pass

    wparam = ((args.delta * 120) << 16) & 0xFFFFFFFFFFFFFFFF
    lparam = ((cy & 0xFFFF) << 16) | (cx & 0xFFFF)
    user32.PostMessageW(post_h, WM_MOUSEWHEEL, wparam, lparam)
    print(f"[osScroll] PostMessageW WM_MOUSEWHEEL hwnd={post_h} delta={args.delta} (ScrollPattern unavailable)")


if __name__ == "__main__":
    main()
