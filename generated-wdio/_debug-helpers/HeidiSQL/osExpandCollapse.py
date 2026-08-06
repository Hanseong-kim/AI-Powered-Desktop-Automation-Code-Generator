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
UIA_ControlTypeProperty = 30003
UIA_ListItem = 50007
UIA_MenuItem = 50011
TreeScope_Descendants = 4
TreeScope_Subtree = 7
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


def listitem_label_point(uia, el):
    """리포트 뷰 행에서 실제로 항목을 여는 지점 — 이름(첫 컬럼) 셀의 중심.

    실측 2026-08-04 (7-Zip SysListView32, poc 프로브):
        행(ListItem) rect = (797, 267, 1405, 286)   중심 x=1101
        이름 셀(Edit)  rect = (800, 267,  833, 286)  중심 x=816
        중심(1101) 더블클릭 -> 제목/행 목록 전부 불변 (아무 일도 안 일어남)
        이름 셀(816) 더블클릭 -> 즉시 진입, 행 1개에서 25개로

    행 rect는 모든 컬럼을 가로지르므로 그 중심은 "수정한 날짜/크기" 컬럼의
    빈 공간이다. 선택(단일 클릭)은 full-row select라 어디를 눌러도 되지만
    활성화(더블클릭)는 이름 셀에서만 일어난다 — 그래서 단일 클릭 스텝은
    멀쩡했고 폴더 진입만 조용히 실패했다.

    좌표를 저장하지 않는다: 재생 시점에 살아 있는 자식 요소의 rect에서
    계산한다(§3의 dynamic ClickablePoint 규칙 그대로). 자식이 없는 행이면
    None을 돌려 기존 clickable_point() 동작을 그대로 쓴다.
    """
    TS_CHILDREN = 2
    UIA_ListItem = 50007
    try:
        if el.CurrentControlType != UIA_ListItem:
            return None
    except Exception:
        return None
    try:
        arr = el.FindAll(TS_CHILDREN, uia.CreateTrueCondition())
    except Exception:
        return None
    best = None
    for i in range(arr.Length if arr else 0):
        try:
            r = arr.GetElement(i).CurrentBoundingRectangle
        except Exception:
            continue
        if r.right <= r.left or r.bottom <= r.top:
            continue
        # 가장 왼쪽 셀 = 이름 컬럼. 아이콘+라벨이 여기 있다.
        if best is None or r.left < best[0]:
            best = (r.left, (r.left + r.right) // 2, (r.top + r.bottom) // 2)
    return (best[1], best[2]) if best else None


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


def send_input_click(uia, el, tag, double=False):
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

    # 리포트 뷰 행은 rect 중심이 빈 컬럼이라 더블클릭이 안 먹는다 —
    # listitem_label_point() 참고 (2026-08-04 실측).
    pt = listitem_label_point(uia, el) or clickable_point(el)
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

    # 두 번째 누름/뗌 — 시스템 더블클릭 간격(기본 500ms) 안에 보내야 앱이
    # 더블클릭으로 인식한다. 여기 간격 합은 ~90ms.
    #
    # 2026-08-04: 이 분기가 없어서 녹화된 더블클릭이 앱에는 단일 클릭으로
    # 도달했다. 네이티브 리스트 행에서 단일 클릭은 "선택"이지 "열기"가 아니다.
    # 원래 설계는 InvokePattern.Invoke()(=기본 동작=열기)에 기대고 있었는데
    # (server.js ListItem 분기 주석, 2026-07-15 실측), 2026-07-24에
    # send_input_click()이 invoke_item() 체인 맨 앞에 붙으면서
    # "if send_input_click(...): return True"가 되어 그 Invoke()가 도달 불가능한
    # 죽은 코드가 됐다. 결과: 7-Zip에서 "3:doubleClick 컴퓨터"가 성공으로
    # 보고되지만 폴더는 안 열리고, "4:doubleClick C:"부터 전부 무너진다
    # (2026-08-04 두 번 연속 동일 재현).
    if double:
        time.sleep(0.05)
        send(MOUSEEVENTF_LEFTDOWN | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK)
        time.sleep(0.04)
        send(MOUSEEVENTF_LEFTUP | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK)

    verb = " double-clicked '" if double else " clicked '"
    print("[COM-SendInput] " + tag + verb + label + "' at (%d,%d)" % (x, y))
    time.sleep(0.05)
    return True


# ── TogglePattern (2026-07-31) ─────────────────────────────────────────────
# 실측(TeamViewer 15.79 WebView2, poc/diag_teamviewer_a11y_wakeup.py [D]):
# 체크박스 2종의 지원 패턴은 정확히 {Toggle, Legacy}뿐 — Invoke도
# SelectionItem도 없다. 기존 클릭 체인(Invoke → SelectionItem → Legacy)에는
# Toggle이 아예 없어서 앞의 둘이 연달아 예외로 떨어진 뒤
# Legacy.Select(TAKESELECTION)이 예외 없이 통과하며 True를 반환했다. 그런데
# 체크박스에서 "셀렉션"은 체크 상태를 바꾸지 않으므로, 재생은 성공으로
# 보고되는데 화면에서는 아무 일도 일어나지 않는다 — 그 체크박스가 띄우는
# 확인 다이얼로그도 당연히 안 뜬다(2026-07-31 클라이언트 피드백 증상 그대로).
# Legacy 폴백보다 앞서 Toggle을 시도하되, ToggleState가 실제로 바뀐 경우에만
# 성공으로 인정한다(거짓 PASS 방지 — 2026-07-13 3차 교훈과 같은 원칙).
UIA_TogglePatternId = 10015


def toggle_item(mod, el, tag):
    try:
        tp = el.GetCurrentPattern(UIA_TogglePatternId).QueryInterface(
            mod.IUIAutomationTogglePattern)
    except Exception:
        return False
    try:
        before = tp.CurrentToggleState
        tp.Toggle()
        time.sleep(0.05)
        after = tp.CurrentToggleState
    except Exception as e:
        print("[" + tag + "] TogglePattern.Toggle() raised: %s" % e, file=sys.stderr)
        return False
    if after != before:
        print("[" + tag + "] TogglePattern.Toggle() %d -> %d" % (before, after))
        return True
    print("[" + tag + "] TogglePattern.Toggle() left ToggleState unchanged (%d)"
          " - falling through to the remaining patterns" % before, file=sys.stderr)
    return False


# ── checkbox 값-변경 검증 (2026-08-04) ───────────────────────────────────────
# 위 toggle_item()은 invoke_item()의 체인 맨 끝(Legacy 폴백보다 앞)에서만
# 쓰인다 — invoke_item()이 맨 앞에서 시도하는 send_input_click()(실제 화면
# 클릭)이 성공하면 그 자리에서 곧바로 True를 반환하므로(§6 "재생은 시각적으로
# 확인 가능해야 한다"는 요구를 만족하는 기본 경로), 대다수 체크박스 클릭은
# toggle_item()까지 내려가지도 않는다. 그 결과 "클릭 자체는 에러 없이 끝났다"만
# 보고 실제로 체크 상태가 바뀌었는지는 아무도 확인하지 않는다 — TeamViewer
# WebView2 토글에서 실측된 것과 같은 종류의 거짓 PASS 위험이 CheckBox
# controlType 전반에 구조적으로 남아 있다(HeidiSQL/PuTTY의 TCheckBox·Button
# 스타일 체크박스도 이 경로를 그대로 탄다 — 2026-08-04 점검 시점엔 아직 실제
# 재생 실패로 드러난 적은 없지만, 다음에 체크박스가 있는 녹화를 재생하면 언제든
# 조용히 터질 수 있는 잠재적 구멍). 시각적 클릭은 그대로 유지하면서(§6), 클릭
# 전후 ToggleState를 비교해 실제로 바뀌었는지만 추가로 검증한다 — 안 바뀌었으면
# toggle_item()의 직접 Toggle() 호출로 한 번 더 보정 시도하고, 그래도 안 바뀌면
# 정직하게 실패로 보고한다(호출부가 exit code로 판단해 _failures에 기록).
def verified_toggle_click(uia, mod, el, tag="osScopedInvoke", double=False):
    try:
        tp = el.GetCurrentPattern(UIA_TogglePatternId).QueryInterface(
            mod.IUIAutomationTogglePattern)
    except Exception:
        # TogglePattern이 없다 = 이 컨트롤은 애초에 체크박스가 아니다(예:
        # CheckBox로 잘못 태깅됐거나 캡처 시점 이후 컨트롤이 바뀐 경우) —
        # 검증할 상태 자체가 없으므로 평범한 클릭으로 폴백한다. 없는 걸
        # 있다고 우기며 거짓 실패를 만들지 않는다.
        return invoke_item(uia, mod, el, double)
    try:
        before = tp.CurrentToggleState
    except Exception:
        return invoke_item(uia, mod, el, double)
    if not invoke_item(uia, mod, el, double):
        return False
    time.sleep(0.05)
    try:
        after = tp.CurrentToggleState
    except Exception as e:
        print(f"[{tag}] click succeeded but ToggleState could not be "
              f"re-read afterward ({e}) — cannot verify, trusting the click", file=sys.stderr)
        return True
    if after != before:
        print(f"[{tag}] checkbox toggled {before} -> {after} (verified)")
        return True
    print(f"[{tag}] click reported success but ToggleState stayed {before} "
          "unchanged — retrying via TogglePattern.Toggle() directly", file=sys.stderr)
    return toggle_item(mod, el, tag)


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
    if toggle_item(mod, el, "osExpandCollapse"):
        return True
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
    # 2026-07-31: ComboBoxEx(HeidiSQL 네트워크 유형)처럼 항목 Name이 전부 빈
    # owner-drawn 드롭다운은 이름으로 지목할 수 없다. 목록 안에서의 순서로만
    # 구별 가능하므로 인덱스를 받는다(좌표가 아니라 구조적 순서 —
    # ListItem/TreeItem/DataItem 슬롯 인덱스와 같은 원리).
    ap.add_argument("--item-index", type=int, default=None)
    ap.add_argument("--item-count", type=int, default=None)
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
    # 2026-08-04 실측(HeidiSQL "환경 설정" -> "파일 및 탭" 탭 전환 직후 콤보
    # 검색): 탭 클릭(osScopedInvoke, 별도 프로세스) 자체는 성공으로 보고되는데,
    # 그 직후 이 프로세스가 즉시 자식 트리를 검색하면 그 탭의 컨트롤(TComboBox
    # 등)이 아직 UIA 트리에 올라오지 않아 매번 "target element not found"였다
    # — osScopedInvoke.py가 렌더링 레이스에 대해 이미 갖고 있는 재시도 예산
    # (2026-07-17/24, "새 사이트(N)" 인라인 이름변경 상자와 같은 근거)을 이
    # 헬퍼의 resolve_target()은 여태 갖고 있지 않았다. 클릭과 같은 예산(10회,
    # 300ms 간격, 최대 ~2.7초)을 그대로 맞춘다.
    target = None
    for attempt in range(10):
        if attempt > 0:
            time.sleep(0.3)
        target = resolve_target(uia, root, sel)
        if target:
            break
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
    # 2026-08-05 (FileZilla 도움말(H) 메뉴 실측 — 2026-08-04 HeidiSQL "더 보기"로
    # 이미 기록됐던 백로그 항목이 그대로 재현): 아래 두 폴백의 조건이
    # "not item_name"뿐이라, **인덱스로 항목을 고르는 이벤트**(item_name이 항상
    # None — 이름이 아니라 위치로 고르는 게 그 방식의 정의다)까지 "이건 서브메뉴가
    # 없는 리프 커맨드였다"로 오판했다. 그 결과 args.item_index가 버젓이 있는데도
    # 그 값을 한 번도 안 보고 트리거만 다시 클릭한 뒤 exit 0으로 "성공" 보고 —
    # 실측 로그: "3:select item #4 FileZilla 정보(A)..." 스텝이
    # "clicked 도움말(H)" + "invoked as a plain command instead"로 끝나고,
    # 정작 "FileZilla 정보"는 눌리지 않았다(§3 거짓 성공).
    #
    # item_index가 있으면 이 폴백을 타면 안 된다. 그런데 단순히 실패시키는 것도
    # 답이 아니다 — 패턴이 없을 뿐 **트리거를 클릭하면 메뉴는 실제로 열린다**
    # (invoke_item이 하는 일이 바로 그것이고, 위 오판 케이스에서 메뉴가 열렸다는
    # 사실 자체가 그 증거다). 그러니 클릭으로 메뉴를 연 뒤 아래 인덱스 기반
    # pool 검색으로 이어가면 원래 의도대로 동작한다.
    ecp = None
    try:
        ecp = target.GetCurrentPattern(UIA_ExpandCollapsePatternId).QueryInterface(
            mod.IUIAutomationExpandCollapsePattern)
    except Exception:
        if args.item_index is None:
            if not item_name and invoke_item(uia, mod, target):
                print("[osExpandCollapse] ExpandCollapsePattern unavailable — invoked as a plain command instead")
                sys.exit(0)
            print("osExpandCollapse: ExpandCollapsePattern not supported on target", file=sys.stderr)
            sys.exit(2)

    # 새 팝업 창(네이티브 TrackPopupMenu 등) 감지용 베이스라인은 Expand() 전에
    # 찍는다 — FileZilla 메뉴바처럼 하위 항목이 그 팝업 서브트리에만 생기는 경우.
    baseline = set(top_windows())

    if ecp is None:
        # 패턴 없음 + 인덱스 있음: 트리거를 클릭해 메뉴를 열고 pool 검색으로 간다.
        if not invoke_item(uia, mod, target):
            print("osExpandCollapse: ExpandCollapsePattern unavailable and the "
                  "trigger could not be clicked either — cannot open the list",
                  file=sys.stderr)
            sys.exit(2)
        print("[osExpandCollapse] ExpandCollapsePattern unavailable — opened the "
              "list by clicking the trigger instead (index-based pick follows)")
        time.sleep(0.4)
    else:
        try:
            if ecp.CurrentExpandCollapseState != ExpandCollapseState_Expanded:
                ecp.Expand()
            else:
                ecp.Collapse()
                time.sleep(0.2)
                ecp.Expand()
        except Exception as e:
            if args.item_index is None and not item_name and invoke_item(uia, mod, target):
                print("[osExpandCollapse] Expand() failed — invoked as a plain command instead")
                sys.exit(0)
            if args.item_index is None:
                print(f"osExpandCollapse: Expand() failed: {e}", file=sys.stderr)
                sys.exit(2)
            # 인덱스 기반 선택은 위와 같은 이유로 여기서 포기하지 않는다.
            if not invoke_item(uia, mod, target):
                print(f"osExpandCollapse: Expand() failed ({e}) and the trigger "
                      "could not be clicked either", file=sys.stderr)
                sys.exit(2)
            print(f"[osExpandCollapse] Expand() failed ({e}) — opened the list by "
                  "clicking the trigger instead (index-based pick follows)")
        time.sleep(0.4)
        try:
            print(f"[osExpandCollapse] state after Expand() = {ecp.CurrentExpandCollapseState}")
        except Exception:
            pass

    # ── 인덱스로 항목 선택 (2026-07-31, owner-drawn ComboBoxEx;
    #    2026-08-04 확장: owner-drawn 팝업 메뉴, HeidiSQL "더 보기") ────────
    # 항목 Name이 전부 빈 드롭다운/메뉴는 이름 조건으로 못 찾는다. 펼친 뒤
    # 보이는 ListItem(콤보) 또는 MenuItem(팝업 메뉴)을 트리 순서대로 모아
    # N번째를 실행한다. 창 서브트리와 새로 뜬 팝업 창을 모두 훑는다(Win32
    # 콤보는 목록을 별도 ComboLBox 창에 그리기도 한다). 어느 컨트롤 타입인지
    # 알려주는 별도 플래그는 없다 — 그 시점에 실제로 열려 있는 게 콤보든
    # 메뉴든 둘 중 하나뿐이므로, 두 타입 다 후보 풀에 넣고 기존 루프가
    # item_count 일치 여부로 걸러내게 둔다. 좌표는 쓰지 않는다.
    if args.item_index is not None:
        time.sleep(0.2)
        li_cond = uia.CreatePropertyCondition(UIA_ControlTypeProperty, UIA_ListItem)
        mi_cond = uia.CreatePropertyCondition(UIA_ControlTypeProperty, UIA_MenuItem)
        pools = []
        for cond, kind in ((li_cond, "ListItem"), (mi_cond, "MenuItem")):
            try:
                pools.append((f"main window ({kind})", root.FindAll(TreeScope_Subtree, cond)))
            except Exception:
                pass
        # 2026-08-05 진단(FileZilla 도움말 메뉴 실측): STEP 1에서 pool이
        # "main window" 두 건만 잡히고 popup 항목이 하나도 안 나왔다 —
        # 즉 Expand() 뒤에도 baseline에 없던 새 최상위 창이 발견되지 않았다.
        # 그게 (a) 팝업이 아예 안 열려서인지 (b) 이미 baseline에 있던
        # 창이라 걸러진 건지 로그만으로 구분이 안 돼서, 후보 수를 남긴다.
        _seen_new, _skipped_baseline = 0, 0
        for h in top_windows():
            if h in baseline:
                _skipped_baseline += 1
                continue
            _seen_new += 1
            try:
                pr = uia.ElementFromHandle(h)
            except Exception:
                continue
            if not pr:
                continue
            for cond, kind in ((li_cond, "ListItem"), (mi_cond, "MenuItem")):
                try:
                    pools.append((f"popup hwnd={h} ({kind})", pr.FindAll(TreeScope_Subtree, cond)))
                except Exception:
                    continue
        print(f"[osExpandCollapse] popup scan: {_seen_new} new top-level window(s) "
              f"after opening, {_skipped_baseline} pre-existing skipped")
        for where, arr in pools:
            if not arr or not arr.Length:
                continue
            if args.item_count and arr.Length != args.item_count:
                print(f"[osExpandCollapse] {where}: {arr.Length} items but the "
                      f"recording saw {args.item_count} — the list changed since "
                      "capture; refusing to pick by position", file=sys.stderr)
                continue
            if args.item_index >= arr.Length:
                print(f"[osExpandCollapse] {where}: index {args.item_index} out of "
                      f"range ({arr.Length} items)", file=sys.stderr)
                continue
            item = arr.GetElement(args.item_index)
            label = ""
            try:
                label = item.CurrentName or ""
            except Exception:
                pass
            if invoke_item(uia, mod, item):
                print(f"[osExpandCollapse] selected item #{args.item_index} of "
                      f"{arr.Length} in {where}"
                      + (f" (name={label!r})" if label else " (unnamed item)"))
                sys.exit(0)
        print(f"osExpandCollapse: could not select item #{args.item_index} in any "
              "open list", file=sys.stderr)
        sys.exit(2)

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
