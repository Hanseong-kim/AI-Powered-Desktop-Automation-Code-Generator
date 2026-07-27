import os, sys, json, base64, argparse, ctypes, time
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
UIA_InvokePatternId = 10000
UIA_ExpandCollapsePatternId = 10005
UIA_SelectionItemPatternId = 10010
UIA_LegacyIAccessiblePatternId = 10018
UIA_SELECTIONFLAG_TAKESELECTION = 1
UIA_ScrollPatternId = 10004
TreeScope_Descendants = 4
ExpandCollapseState_Expanded = 1

user32 = ctypes.windll.user32

# ── dynamic ClickablePoint + SendInput (2026-07-24) ─────────────────────────
# 녹화된 좌표는 여기 어디에도 들어오지 않는다. 좌표는 매 실행마다 UIA가 방금
# resolve한 요소로부터 계산해 즉시 소비하고 버린다 — 창이 이동/리사이즈되거나
# 해상도가 바뀌어도 항상 새로 계산되므로 §3 금지의 원래 취지(저장된 좌표가
# 재생 시점에 어긋나는 것)를 건드리지 않는다.
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
    # agent.py의 _enable_per_monitor_dpi_awareness()와 동일한 근거로 필수:
    # 파이썬 프로세스는 기본 DPI-unaware라 125% 스케일 환경에서 UIA가 돌려주는
    # rect/ClickablePoint가 가상화된 논리 좌표로 오는 반면 SendInput의 절대
    # 좌표계는 물리 픽셀이다 — 격상하지 않으면 두 좌표계가 어긋나 엉뚱한
    # 지점을 클릭한다. UIA 객체를 만들기 전에 호출해야 한다.
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
    """UIA가 지금 이 순간 계산한 클릭 지점. 실패하면 None."""
    try:
        res = el.GetClickablePoint()
    except Exception:
        res = None
    # comtypes는 [out] 파라미터 2개(POINT, BOOL)를 튜플로 돌려주지만 바인딩
    # 버전에 따라 POINT 하나만 오는 경우도 있어 양쪽을 모두 받아준다.
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


def _same_or_descendant(uia, ancestor, el, max_up=6):
    cur = el
    try:
        walker = uia.RawViewWalker
    except Exception:
        walker = None
    for _ in range(max_up + 1):
        try:
            if uia.CompareElements(ancestor, cur):
                return True
        except Exception:
            return False
        if not walker:
            return False
        try:
            cur = walker.GetParentElement(cur)
        except Exception:
            return False
        if not cur:
            return False
    return False


def send_input_click(uia, el, tag):
    """UIA로 방금 찾은 요소를 실제 마우스 입력으로 클릭한다.

    안전 검증을 하나라도 통과 못 하면 사유를 남기고 False — 호출자는 기존
    프로그래매틱 Invoke()/Select() 체인으로 폴백한다(에러로 튕기는 것보다
    비시각적으로라도 동작하는 게 낫다는 2026-07-24 지시).
    """
    def bail(reason):
        print("[COM-SendInput] fallback: " + reason + " - using programmatic Invoke/Select", file=sys.stderr)
        return False

    if os.environ.get("QAFORGE_COM_CLICK") == "invoke":
        return bail("forced-programmatic (QAFORGE_COM_CLICK=invoke)")
    try:
        if el.CurrentIsOffscreen:
            return bail("offscreen")
    except Exception:
        pass

    # 라벨은 반드시 주입 **전에** 읽는다. 메뉴 항목/다이얼로그 버튼은 클릭
    # 즉시 파괴돼 그 뒤의 프로퍼티 읽기가 실패하고, 로그가 '?'로 남아 추적이
    # 불가능해진다(2026-07-24 FileZilla 실측: 메뉴 항목/예(Y)/취소 전부 '?',
    # 살아남는 콤보 화살표만 '닫기'로 정상 출력).
    try:
        label = el.CurrentName or el.CurrentAutomationId or "?"
    except Exception:
        label = "?"

    pt = clickable_point(el)
    if not pt:
        return bail("no-clickable-point")
    x, y = pt
    vx = user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
    vy = user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
    vw = user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)
    vh = user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)
    if vw <= 1 or vh <= 1 or not (vx <= x < vx + vw and vy <= y < vy + vh):
        return bail("point-outside-virtual-screen (%d,%d)" % (x, y))

    # 그 지점이 정말 대상 요소의 것인지 두 겹으로 확인한다. 2026-07-15에
    # osScopedInvoke가 완전히 남남인 사용자 창(탐색기/VS Code)을 클릭하고
    # "성공"으로 보고한 사고, 2026-07-13에 클릭 지점이 요소 rect 밖이라
    # 물리적으로 no-op이던 이벤트가 재생 때는 요소 중심을 클릭해 엉뚱한 패널을
    # 연 트랩 — 둘 다 이 검증으로 구조적으로 막힌다.
    winpt = wintypes.POINT(int(x), int(y))
    try:
        hwnd_at = user32.WindowFromPoint(winpt)
        pid_at = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd_at, ctypes.byref(pid_at))
        if pid_at.value != el.CurrentProcessId:
            return bail("covered-by-other-window (pid %d at point, target pid %d)"
                        % (pid_at.value, el.CurrentProcessId))
    except Exception as e:
        return bail("window-hit-test-failed (%s)" % e)

    try:
        at_point = uia.ElementFromPoint(winpt)
    except Exception as e:
        return bail("element-from-point-failed (%s)" % e)
    if not at_point or not _same_or_descendant(uia, el, at_point):
        return bail("point-resolves-elsewhere")

    nx = int(round((x - vx) * 65535.0 / (vw - 1)))
    ny = int(round((y - vy) * 65535.0 / (vh - 1)))

    def send(flags):
        inp = INPUT(type=INPUT_MOUSE)
        inp.mi = MOUSEINPUT(nx, ny, 0, flags, 0, 0)
        return user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))

    # 사람이 눈으로 따라갈 수 있도록 이동/누름/뗌 사이에 간격을 둔다(§6).
    if not send(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK):
        return bail("SendInput(move) rejected")
    time.sleep(0.04)
    if not send(MOUSEEVENTF_LEFTDOWN | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK):
        return bail("SendInput(down) rejected")
    time.sleep(0.04)
    send(MOUSEEVENTF_LEFTUP | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK)

    print("[COM-SendInput] " + tag + " clicked '" + label + "' at (%d,%d)" % (x, y))
    time.sleep(0.05)
    return True


def top_windows():
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            found.append(hwnd)
        return True

    user32.EnumWindows(cb, 0)
    return found


# 2026-07-24: WAD의 element/click은 오프스크린(스크롤로 안 보이는) 요소를
# 자동으로 스크롤-인-뷰한 뒤 클릭한다(암묵적 동작) — 이 파일이 다루는 COM
# 경로(WAD 세션이 못 보는 팝업/새 창의 좁은 예외)에는 그 자동 스크롤이 없어,
# Expand() 이후 메뉴/드롭다운 항목이 화면 밖에 있으면 Invoke 전에 조상
# ScrollPattern으로 끌어와야 한다. 좌표는 쓰지 않는다(§3).
def find_scrollable_ancestor(uia, el, max_up=8):
    try:
        walker = uia.RawViewWalker
    except Exception:
        return None
    cur = el
    for _ in range(max_up):
        try:
            cur.GetCurrentPattern(UIA_ScrollPatternId)
            return cur
        except Exception:
            pass
        try:
            cur = walker.GetParentElement(cur)
            if not cur:
                return None
        except Exception:
            return None
    return None


def ensure_visible(uia, mod, el):
    try:
        if not el.CurrentIsOffscreen:
            return
    except Exception:
        return
    print("[osExpandCollapse] target is offscreen — attempting scroll-into-view via ancestor ScrollPattern", file=sys.stderr)
    ancestor = find_scrollable_ancestor(uia, el)
    if not ancestor:
        return
    try:
        sp = ancestor.GetCurrentPattern(UIA_ScrollPatternId).QueryInterface(mod.IUIAutomationScrollPattern)
        sp.SetScrollPercent(50.0, 50.0)
        time.sleep(0.2)
    except Exception:
        pass


def field_conds(uia, sel):
    conds = []
    if sel.get("automationId"):
        conds.append(uia.CreatePropertyCondition(UIA_AutomationIdProperty, sel["automationId"]))
    if sel.get("name"):
        conds.append(uia.CreatePropertyCondition(UIA_NameProperty, sel["name"]))
    if sel.get("className"):
        conds.append(uia.CreatePropertyCondition(UIA_ClassNameProperty, sel["className"]))
    return conds


def resolve_target(uia, root, sel):
    # PuTTY류 다이얼로그는 카테고리 패널마다 숫자 AutomationId를 재사용한다
    # (2026-07-13 실측: id=1044가 라디오 버튼과 "Proxy type:" 콤보에 동시에 붙음)
    # — 있는 필드를 전부 AND로 묶은 조건을 먼저 시도해 모호성을 없애고, 그래도
    # 못 찾으면 필드별 단독 조건으로 폴백.
    conds = field_conds(uia, sel)
    if not conds:
        return None
    if len(conds) > 1:
        combined = conds[0]
        for c in conds[1:]:
            combined = uia.CreateAndCondition(combined, c)
        try:
            t = root.FindFirst(TreeScope_Descendants, combined)
            if t:
                return t
        except Exception:
            pass
    for c in conds:
        try:
            t = root.FindFirst(TreeScope_Descendants, c)
            if t:
                return t
        except Exception:
            continue
    return None


def invoke_item(uia, mod, el):
    ensure_visible(uia, mod, el)
    try:
        el.SetFocus()
    except Exception:
        pass
    # 시각적 재생 우선(2026-07-24, §6) — 성공하면 반드시 여기서 반환한다.
    # 이어서 Invoke()까지 부르면 같은 동작이 두 번 실행된다.
    if send_input_click(uia, el, "osExpandCollapse"):
        return True
    try:
        el.GetCurrentPattern(UIA_InvokePatternId).QueryInterface(mod.IUIAutomationInvokePattern).Invoke()
        return True
    except Exception:
        pass
    try:
        el.GetCurrentPattern(UIA_SelectionItemPatternId).QueryInterface(mod.IUIAutomationSelectionItemPattern).Select()
        return True
    except Exception:
        pass
    try:
        legacy = el.GetCurrentPattern(UIA_LegacyIAccessiblePatternId).QueryInterface(mod.IUIAutomationLegacyIAccessiblePattern)
        try:
            legacy.Select(UIA_SELECTIONFLAG_TAKESELECTION)
            return True
        except Exception:
            pass
        legacy.DoDefaultAction()
        return True
    except Exception:
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hwnd", type=int, required=True)
    ap.add_argument("--sel-b64", required=True)
    ap.add_argument("--item-name-b64", default=None)
    args = ap.parse_args()

    if not args.hwnd:
        print("osExpandCollapse: --hwnd is required", file=sys.stderr)
        sys.exit(2)

    enable_per_monitor_dpi()
    comtypes.CoInitialize()
    mod = comtypes.client.GetModule("UIAutomationCore.dll")
    uia = comtypes.client.CreateObject(
        "{ff48dba4-60ef-4201-aa87-54103eef594e}", interface=mod.IUIAutomation
    )

    # 2026-07-24 실측(FileZilla 재현): 방금 그 hwnd에 WinAppDriver scoped
    # session이 막 생성된 직후(재생 로그: "scoped session on 0x301010 ready
    # in 1354ms" 다음 STEP에서 곧바로) 이 프로세스의 별도 IUIAutomation COM
    # 클라이언트가 같은 hwnd에 ElementFromHandle()을 호출하면 간헐적으로
    # COMError(-2147220991)가 난다 — WinAppDriver의 내부 UIA 클라이언트가 그
    # 창에 이벤트 구독을 마치기 전 레이스로 추정(에러 메시지 자체가 "이벤트
    # 구독자를 불러올 수 없음"). 셀렉터/로직 문제가 아니라 타이밍 문제이므로,
    # 실패로 단정하기 전에 짧게 재시도한다(osScopedInvoke.py의 4회 재시도와
    # 같은 근거).
    root = None
    for attempt in range(4):
        if attempt > 0:
            time.sleep(0.3)
        try:
            root = uia.ElementFromHandle(args.hwnd)
        except Exception as e:
            root = None
            if attempt == 3:
                print(f"osExpandCollapse: ElementFromHandle failed: {e}", file=sys.stderr)
        if root:
            break
    if not root:
        print("osExpandCollapse: ElementFromHandle failed", file=sys.stderr)
        sys.exit(2)

    sel = json.loads(base64.b64decode(args.sel_b64).decode("utf-8"))
    target = resolve_target(uia, root, sel)
    if not target:
        print(f"osExpandCollapse: target element not found (sel={args.sel_b64})", file=sys.stderr)
        sys.exit(2)

    item_name = None
    if args.item_name_b64:
        item_name = base64.b64decode(args.item_name_b64).decode("utf-8")

    # 2026-07-23 실측(FileZilla "네트워크 구성 마법사(N)..." 재현): agent.py의
    # expandCollapse 태깅은 UIA의 "IsExpandCollapsePatternAvailable" 구조적
    # 응답만 보는데, wx는 서브메뉴가 없는 리프 커맨드 MenuItem에도 이걸 true로
    # 보고하는 경우가 있다(실제 GetCurrentPattern()/Expand() 호출 시점에야
    # 드러남 — 캡처 시점 검사와 재생 시점 COM 호출 결과가 다름). item_name이
    # 없는 단독 토글 이벤트(병합 안 된 경우)에서 이게 벌어지면, 그 클릭은
    # 원래 "메뉴 펼치기"가 아니라 "커맨드 실행"이었다는 뜻이므로, 실패로
    # 끝내는 대신 평범한 클릭(Invoke/Select/LegacyIAccessible)으로 폴백해
    # 실제 유저가 한 동작(메뉴 항목 실행)을 재현한다. item_name이 있는
    # (병합된 진짜 서브메뉴) 경우는 mergeExpandCollapseClicks의 rootHwndHex
    # 창-경계 가드가 이제 이런 리프 커맨드를 트리거로 병합하지 않으므로
    # 여기까지 오지 않는다 — 그 경로는 기존처럼 실패로 남겨 무엇이 잘못됐는지
    # 숨기지 않는다.
    try:
        ecp = target.GetCurrentPattern(UIA_ExpandCollapsePatternId).QueryInterface(
            mod.IUIAutomationExpandCollapsePattern)
    except Exception:
        if not item_name and invoke_item(uia, mod, target):
            print("[osExpandCollapse] ExpandCollapsePattern unavailable — invoked as a plain command instead")
            sys.exit(0)
        print("osExpandCollapse: ExpandCollapsePattern not supported on target", file=sys.stderr)
        sys.exit(2)

    # 새 팝업 창(네이티브 TrackPopupMenu 등) 감지용 베이스라인은 Expand() 전에
    # 찍는다 — FileZilla 메뉴바처럼 하위 항목이 그 팝업 서브트리에만 생기는 경우.
    baseline = set(top_windows())

    try:
        if ecp.CurrentExpandCollapseState != ExpandCollapseState_Expanded:
            ecp.Expand()
        else:
            ecp.Collapse()
            time.sleep(0.2)
            ecp.Expand()
    except Exception as e:
        if not item_name and invoke_item(uia, mod, target):
            print("[osExpandCollapse] Expand() failed — invoked as a plain command instead")
            sys.exit(0)
        print(f"osExpandCollapse: Expand() failed: {e}", file=sys.stderr)
        sys.exit(2)
    time.sleep(0.4)
    print(f"[osExpandCollapse] state after Expand() = {ecp.CurrentExpandCollapseState}")

    if not item_name:
        # 항목 선택 없이 펼치기/접기 자체가 목적인 이벤트(예: 트리 +- 토글).
        sys.exit(0)

    item_cond = uia.CreatePropertyCondition(UIA_NameProperty, item_name)

    # (a) 같은 창 서브트리에서 찾기 — PuTTY ComboBox처럼 드롭다운 항목이 세션
    #     스코프 안에 있는 경우(2026-07-13 실측: 'SOCKS 5' 발견됨).
    try:
        item = root.FindFirst(TreeScope_Descendants, item_cond)
    except Exception:
        item = None
    if item and invoke_item(uia, mod, item):
        print(f"[osExpandCollapse] invoked '{item_name}' under main window subtree")
        sys.exit(0)

    # (b) Expand() 이후 새로 뜬 최상위 창 서브트리 — FileZilla 메뉴바처럼 하위
    #     항목이 네이티브 팝업(#32768 등)에만 있는 경우.
    time.sleep(0.2)
    for h in top_windows():
        if h in baseline:
            continue
        try:
            popup_root = uia.ElementFromHandle(h)
            if not popup_root:
                continue
            item = popup_root.FindFirst(TreeScope_Descendants, item_cond)
            if item and invoke_item(uia, mod, item):
                print(f"[osExpandCollapse] invoked '{item_name}' under new popup hwnd={h}")
                sys.exit(0)
        except Exception:
            continue

    print(f"osExpandCollapse: item '{item_name}' not found under main window or any new popup window", file=sys.stderr)
    sys.exit(2)


if __name__ == "__main__":
    main()
