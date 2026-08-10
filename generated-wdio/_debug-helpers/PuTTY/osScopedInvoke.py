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
UIA_ControlTypeProperty = 30003
UIA_InvokePatternId = 10000
UIA_SelectionItemPatternId = 10010
UIA_ValuePatternId = 10002
UIA_LegacyIAccessiblePatternId = 10018
# 2026-07-23 실측(FileZilla Site Manager "고급/전송 설정/문자셋/일반" 카테고리
# 탭 재현, controlType=50019=TabItem): wxWidgets의 커스텀-드로잉 Notebook 탭은
# UIA TabItem으로 노출되지만 InvokePattern도 SelectionItemPattern도 실제로는
# 구현하지 않는다(QueryInterface는 성공 조건에 따라 예외 없이 통과할 때도 있으나
# Invoke()/Select() 호출 자체가 아무 효과 없이 조용히 끝나거나 예외를 던짐 —
# 둘 다 두 로그에서 8회 재시도 전부 실패로 확인됨). wx는 MSAA(IAccessible) 쪽은
# 안정적으로 구현하므로 LegacyIAccessiblePattern(Select 우선, 안 되면
# DoDefaultAction)을 좌표 없는 마지막 폴백으로 시도한다 — 여전히 픽셀 좌표는
# 전혀 쓰지 않는다(§3 Hard Rules 준수, UIA/MSAA 프로퍼티 기반 탐색만 사용).
UIA_SELECTIONFLAG_TAKESELECTION = 1
# 2026-07-17 (2차) 실측(FileZilla Site Manager 재생 타임스탬프 진단):
# osScopedInvoke가 "target not found"로 보고한 실패 중 다수가 사실은 요소를
# 매번 찾았는데(item=found) Invoke/SelectionItemPattern 둘 다 미지원이라
# invoke_item()이 False를 반환한 것이었다(Tree 컨테이너 자체, Edit 필드 클릭 —
# 둘 다 그 패턴들을 구조적으로 지원 안 함). 이런 컨트롤에 대한 "클릭"의 실제
# 의도는 포커스 이동뿐이므로, 이 5종 + SetFocus 성공 시에만 성공으로 인정한다.
# Button/MenuItem/TreeItem/ListItem 등 실제 실행 가능한 컨트롤은 이 목록에
# 없으므로 여전히 Invoke/Select 성공을 요구한다(거짓 PASS 방지 — 2026-07-13
# 3차 교훈: UIA 패턴 "지원 여부"만으로 태깅하면 과다신호가 되므로 실증된
# ControlType으로만 좁힌다).
PASSIVE_CONTROL_TYPES = {50004, 50030, 50033, 50018, 50023}  # Edit, Document, Pane, Tab, Tree
UIA_ScrollPatternId = 10004
TreeScope_Descendants = 4
# Element(1)|Children(2)|Descendants(4) — TreeScope_Descendants alone can
# never match the root element being searched from (UIA standard behavior),
# so a captured click whose target IS the window itself (e.g. a dialog's own
# className="#32770" root, no automationId) is structurally unfindable with
# Descendants-only scope. Confirmed 2026-07-16 (FileZilla Site Manager
# dialog click failing "target not found" despite the window genuinely
# being open) — use Subtree everywhere a target might be a window root.
TreeScope_Subtree = 7

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


def force_foreground(hwnd):
    """AttachThreadInput 트릭으로 포그라운드 잠금을 우회해 hwnd를 실제 OS
    포그라운드/활성 창으로 올린다 — SIMPLE_HEADER의 osActivate.ps1
    (WinActivate.Force)과 같은 로직이지만, 별도 프로세스를 띄우고 그 프로세스가
    끝나길 기다리는 대신 이 프로세스 안에서 곧바로 이어지는 Expand()/Invoke()
    호출과 같은 프로세스 수명 안에서 실행된다.
    2026-08-08 실측(FileZilla "도움말(H)" 메뉴, launchApp 직후 첫 액션):
    launchApp()이 별도 PowerShell 프로세스(osActivate)로 창을 활성화했는데도
    Expand()가 매번 state=0(펼쳐지지 않음), 새 팝업 0개로 실패했다 — PowerShell
    프로세스가 활성화 직후 종료되면 Windows가 포그라운드를 그 부모(터미널)에게
    돌려주는 것으로 보여, 별도 프로세스가 끝나는 순간 활성화 효과가 사라진다.
    실제 화면 클릭(COM-SendInput)을 거치는 스텝들은 클릭 좌표 아래 창이 자연히
    포그라운드가 되므로 이 레이스를 겪지 않는다 — Expand()처럼 시각적 동작이
    없는 패턴 API만 문제다.
    """
    if not hwnd:
        return
    try:
        SW_RESTORE = 9
        user32.ShowWindow(hwnd, SW_RESTORE)
        fg = user32.GetForegroundWindow()
        fg_tid = user32.GetWindowThreadProcessId(fg, None)
        me_tid = ctypes.windll.kernel32.GetCurrentThreadId()
        attached = False
        if fg_tid and fg_tid != me_tid:
            attached = bool(user32.AttachThreadInput(me_tid, fg_tid, True))
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
        user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0003)   # HWND_TOPMOST, NOMOVE|NOSIZE
        user32.SetWindowPos(hwnd, -2, 0, 0, 0, 0, 0x0003)   # HWND_NOTOPMOST
        if attached:
            user32.AttachThreadInput(me_tid, fg_tid, False)
        time.sleep(0.15)
    except Exception:
        pass


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


def window_title(hwnd):
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def owner_window_for(main_h, main_pid, owner_title):
    """The same-PID top-level window this event was actually recorded in.

    Cross-window steps used to search the MAIN window first and only then
    other top-level windows. That is wrong whenever the recorded selector is
    weak enough to also match something in the main window. Measured
    2026-08-03 (PuTTY): step '13:click 닫기' belongs to the 'PuTTY User
    Manual' window and carries NO AutomationId and NO ClassName, so its
    selector is the bare name 닫기 — which the main PuTTY Configuration
    window ALSO contains (two ComboBox DropDown arrows are named 닫기, and a
    Korean titlebar Close button is Button[@Name="닫기"] as well). The main
    window search matched 2 elements, picked one by recorded position, and
    closed PuTTY Configuration — after which every later step failed with
    click-not-found.

    Returning the recorded window here lets the caller search it FIRST. Match
    on containment because titles carry volatile suffixes; skip when the hint
    matches the main window (the ordinary same-window case) so nothing about
    the existing path changes there.
    """
    if not owner_title:
        return None
    main_title = window_title(main_h)
    if owner_title in main_title or main_title in owner_title:
        return None
    for h in top_windows():
        if h == main_h:
            continue
        cand_pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(h, ctypes.byref(cand_pid))
        if cand_pid.value != main_pid:
            continue
        if owner_title in window_title(h):
            return h
    return None


def resolve_cond(uia, sel):
    conds = []
    if sel.get("automationId"):
        conds.append(uia.CreatePropertyCondition(UIA_AutomationIdProperty, sel["automationId"]))
    if sel.get("name"):
        conds.append(uia.CreatePropertyCondition(UIA_NameProperty, sel["name"]))
    if sel.get("className"):
        conds.append(uia.CreatePropertyCondition(UIA_ClassNameProperty, sel["className"]))
    # 2026-08-10 (FileZilla "C:" 트리 vs 리스트 혼동 실측): comSafeTarget()이
    # automationId/className 둘 다 없을 때만 controlTypeId를 싣는다 — 같은
    # 화면에 같은 Name의 owner-drawn 컨트롤이 둘 이상(TreeItem/ListItem) 있을
    # 때 FindFirst가 아무거나 먼저 걸리는 걸 막는 추가 AND 조건.
    if sel.get("controlTypeId") is not None:
        conds.append(uia.CreatePropertyCondition(
            UIA_ControlTypeProperty, int(sel["controlTypeId"])))
    if not conds:
        return None
    cond = conds[0]
    for c in conds[1:]:
        cond = uia.CreateAndCondition(cond, c)
    # 2026-08-08 실측 (FileZilla "파일 검색" 창 STEP 37 "click 닫기"): 그
    # 창의 타이틀바 닫기 버튼은 automationId/className이 전혀 없어 셀렉터가
    # Name="닫기" 단독이 된다. 같은 창 안의 ComboBox 드롭다운 화살표
    # (automationId="DropDown")도 펼쳐진 상태에서는 Name이 "닫기"로 바뀐다
    # (owner_window_for()의 2026-08-03 PuTTY 코멘트에 이미 같은 충돌이
    # 기록돼 있음). 이전 스텝들이 드롭다운 3개를 펼쳐놓은 채였던 이번 케이스는
    # FindFirst가 그 화살표 중 하나를 먼저 찾아 "성공"으로 보고했지만 실제
    # 창은 닫히지 않았다. 드롭다운 화살표는 자기 자신을 가리킬 때 항상
    # automationId="DropDown"을 셀렉터에 싣는다(comSafeTarget의
    # isComboDropDownArrow) — 그러니 automationId/className이 전혀 없는
    # (즉 진짜로 모호한) 순수 Name 매칭에서만 그 화살표들을 후보에서 제외한다.
    if not sel.get("automationId") and not sel.get("className") and sel.get("name"):
        try:
            not_dropdown = uia.CreateNotCondition(
                uia.CreatePropertyCondition(UIA_AutomationIdProperty, "DropDown"))
            cond = uia.CreateAndCondition(cond, not_dropdown)
        except Exception:
            pass
    return cond


# 2026-07-24: osExpandCollapse.py와 동일한 근거로 이식 — 이 파일의 COM 경로
# (owned 다이얼로그/새 팝업창의 좁은 예외)에는 WAD의 암묵적 오프스크린
# 스크롤-인-뷰가 없다. Invoke/Select/LegacyIAccessible 시도 전에 조상
# ScrollPattern으로 한 번 끌어와 본다 — 좌표는 쓰지 않는다(§3).
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
    print("[osScopedInvoke] target is offscreen — attempting scroll-into-view via ancestor ScrollPattern", file=sys.stderr)
    ancestor = find_scrollable_ancestor(uia, el)
    if not ancestor:
        return
    try:
        sp = ancestor.GetCurrentPattern(UIA_ScrollPatternId).QueryInterface(mod.IUIAutomationScrollPattern)
        sp.SetScrollPercent(50.0, 50.0)
        time.sleep(0.2)
    except Exception:
        pass


def invoke_item(uia, mod, el, double=False):
    ensure_visible(uia, mod, el)
    focus_ok = False
    try:
        el.SetFocus()
        focus_ok = True
    except Exception:
        pass
    # 시각적 재생 우선(2026-07-24, §6) — 성공하면 반드시 여기서 반환한다.
    # 이어서 Invoke()까지 부르면 같은 동작이 두 번 실행된다.
    # double=True면 두 번 누른다 — 아래 Invoke() 폴백은 기본 동작(=열기)이라
    # 더블클릭 의미를 그대로 만족하므로 폴백 체인은 손대지 않는다.
    if send_input_click(uia, el, "osScopedInvoke", double):
        return True
    try:
        el.GetCurrentPattern(UIA_InvokePatternId).QueryInterface(mod.IUIAutomationInvokePattern).Invoke()
        return True
    except Exception:
        pass
    if toggle_item(mod, el, "osScopedInvoke"):
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
        pass
    try:
        ctrl_type = el.CurrentControlType
    except Exception:
        ctrl_type = None
    if focus_ok and ctrl_type in PASSIVE_CONTROL_TYPES:
        return True
    print(f"[osScopedInvoke] found element (controlType={ctrl_type}) but no actionable pattern (Invoke/Select/LegacyIAccessible) succeeded", file=sys.stderr)
    return False


# 2026-07-24 실측(PuTTY): 콤보박스 드롭다운 화살표의 automationId는 창 안의
# 모든 콤보가 "DropDown"으로 공유한다 — 상태 의존 Name("닫기")은 2026-07-14
# 가드가 이미 떼어내므로 AND 조건으로 좁힐 수도 없다. FindFirst는 트리의 첫
# 번째(= Translation 패널의 "Remote character set") 화살표만 계속 잡았고,
# Proxy 패널 항목을 고르는 스텝 두 개가 "같은 콤보를 반복해서 여는" 증상으로
# 실패했다(재생 로그가 매번 동일한 좌표 (1223,412)를 찍은 것이 증거).
# PuTTY류 다이얼로그는 비활성 패널의 컨트롤을 화면에서 내리므로, 후보가
# 여럿이면 offscreen이 아닌 것을 고른다. 여러 후보를 차례로 클릭해보는 방식은
# 쓰지 않는다 — 실패한 후보가 드롭다운을 열어둔 채로 남아 다음 후보 클릭이
# 그 팝업 해제에 먹히기 때문.
def elements_from(arr):
    cands = []
    if not arr:
        return cands
    for i in range(arr.Length):
        try:
            cands.append(arr.GetElement(i))
        except Exception:
            pass
    return cands


# 2026-07-29 (HeidiSQL: 두 콤보박스의 드롭다운 화살표가 automationId="DropDown"을
# 공유): pick_trigger의 기존 "offscreen 제외 후 첫 번째" 방식은 PuTTY 전용
# 가정(비활성 패널 컨트롤이 화면 밖으로 밀려남)에 의존한다 — HeidiSQL은 두
# 콤보가 동시에 진짜로 화면에 떠 있어 이 필터가 아무 것도 못 거른다. 캡처된
# window-relative Y(relY)를 현재 창의 실제 위치에 매 실행마다 다시 더해
# 절대 Y로 변환하고, 후보들 중 그 위치에 가장 가까운 rect.top을 가진 걸
# 고른다 — 저장된 화면 좌표를 클릭에 쓰는 게 아니라(§3 금지 대상과 다름),
# 이미 구조적으로 찾아낸 후보들 사이에서 어느 걸 고를지 정하는 신호로만
# 쓴다(§3 2026-07-24 재정의: 매 실행 재계산·즉시 소비·저장 안 함 — 이미
# 승인된 dynamic ClickablePoint 예외와 같은 성격).
def pick_by_position(cands, win_top, rel_y):
    if not cands:
        return None
    if len(cands) == 1 or win_top is None or rel_y is None:
        return cands[0]
    target_y = win_top + rel_y
    best, best_dist = None, None
    for c in cands:
        try:
            r = c.CurrentBoundingRectangle
            d = abs(r.top - target_y)
        except Exception:
            continue
        if best_dist is None or d < best_dist:
            best, best_dist = c, d
    return best if best is not None else cands[0]


def pick_trigger(uia, root, cond, win_top=None, rel_y=None):
    try:
        arr = root.FindAll(TreeScope_Subtree, cond)
    except Exception:
        arr = None
    cands = elements_from(arr)
    if not cands:
        return None
    if len(cands) == 1:
        return cands[0]
    visible = []
    for c in cands:
        try:
            if not c.CurrentIsOffscreen:
                visible.append(c)
        except Exception:
            visible.append(c)
    pool = visible or cands
    if win_top is not None and rel_y is not None:
        picked = pick_by_position(pool, win_top, rel_y)
        if picked is not None:
            print(f"[osScopedInvoke] trigger selector matched {len(cands)} elements "
                  f"({len(visible)} on screen) — disambiguated by recorded position")
            return picked
    print(f"[osScopedInvoke] WARN trigger selector matched {len(cands)} elements "
          f"({len(visible)} on screen) — the automationId is reused across "
          f"controls; picking the first on-screen one")
    return pool[0]


# 2026-07-17: owned 다이얼로그(WAD가 scoped session을 거부하는 창)에 타이핑하기
# 위한 COM 경로. 기존에는 getWindowSession()이 owned 창을 만나면 WinAppDriver
# Root 세션 REST로 전체 데스크톱 XPath 검색을 폴백으로 썼는데, 실측(2026-07-17
# FileZilla 다이얼로그 진단): 이 Root-세션 REST 호출은 쿼리 내용/매치 여부와
# 무관하게 매번 15~20초가 걸린다(빈 결과조차 15.6초) — WinAppDriver 3.5.2의
# Root 세션 자체가 모든 element 조회에 고정 비용을 갖는 것으로 보임. hwnd는
# 이미 EnumWindows로 알고 있으므로, 같은 COM 스택(osScopedInvoke의 클릭 경로와
# 동일)으로 즉시 타이핑하면 이 15~20초를 완전히 우회한다.
# 2026-07-24 실측(poc/diag_filezilla_rename.py, FileZilla Site Manager):
# "새 사이트(N)" 직후 뜨는 인라인 이름변경 상자는
#   - 캡처 시점에는 automationId="1"로 기록되지만
#   - 재생 시점의 라이브 요소는 automationId="" (name도 "")
# 즉 그 id는 런타임에 변하는 값이라 셀렉터로 절대 매칭되지 않는다(ListItem
# 슬롯 인덱스와 같은 부류). 대신 이 상자는 **나타나는 순간 항상 키보드 포커스를
# 가진다**(인라인 rename의 정의상 그렇다) — 좌표를 쓰지 않고 이 상자를 집는
# 가장 신뢰할 수 있는 방법이다. 타이핑 대상을 못 찾았을 때만, 그리고 포커스
# 요소가 우리 프로세스의 입력 컨트롤일 때만 쓴다(남의 창에 타이핑 방지).
INPUT_CONTROL_TYPES = {50004, 50030, 50003}  # Edit, Document, ComboBox


def focused_input(uia, main_pid):
    try:
        el = uia.GetFocusedElement()
    except Exception:
        return None
    if not el:
        return None
    try:
        if el.CurrentProcessId != main_pid:
            return None
        if el.CurrentControlType not in INPUT_CONTROL_TYPES:
            return None
        aid = el.CurrentAutomationId
        cls = el.CurrentClassName
    except Exception:
        return None
    print(f"[osScopedInvoke] target not found — falling back to the focused "
          f"input control (id='{aid}' class='{cls}'); an inline rename box "
          f"exposes a runtime-varying AutomationId, so focus is the stable handle")
    return el


def type_item(uia, mod, el, text):
    ensure_visible(uia, mod, el)
    try:
        el.SetFocus()
    except Exception:
        pass
    # 2026-07-17 (2차) 실측: 인라인 이름변경 편집 상자에 입력된 값은 물리
    # 캡처에서 트레일링 개행("d\n")을 포함한다(사용자가 Enter로 확정) — 이걸
    # 그대로 SetValue에 넣으면 개행이 리터럴 문자로 들어갈 뿐 Enter 키로
    # 동작하지 않아 편집 상자가 미확정 상태로 남고, 그 상태에서 같은
    # 다이얼로그의 다른 서브트리(탭 패널 등)가 UIA 검색에서 안 잡히는 게
    # 실측으로 확인됨(FileZilla Site Manager). 트레일링 개행은 스트립 —
    # 실제 Enter 키 주입은 이 상태에서 어디까지 필요한지 GUI 재검증 후 별도로.
    value = text[:-1] if text.endswith('\n') else text
    try:
        el.GetCurrentPattern(UIA_ValuePatternId).QueryInterface(mod.IUIAutomationValuePattern).SetValue(value)
        return True
    except Exception:
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hwnd", type=int, required=True)
    ap.add_argument("--sel-b64", required=True)
    ap.add_argument("--trigger-sel-b64", default=None)
    ap.add_argument("--text-b64", default=None)
    # 2026-07-29 (HeidiSQL DropDown 재사용): 구조적으로 여러 후보가 걸릴 때
    # 어느 걸 고를지에만 쓰는 위치 힌트 — 클릭 좌표 자체가 아니다(§3 참고,
    # pick_by_position 주석). rel-y/trigger-rel-y는 캡처 시점의 창-상대 Y이고,
    # 매 실행 현재 창 위치에 다시 더해서만 쓴다.
    ap.add_argument("--rel-y", type=int, default=None)
    ap.add_argument("--trigger-rel-y", type=int, default=None)
    # 이 이벤트가 실제로 녹화된 창의 제목 — owner_window_for() 참고.
    ap.add_argument("--owner-title-b64", default=None)
    # 녹화된 동작이 더블클릭이었는가. 네이티브 리스트 행에서 단일 클릭은
    # 선택일 뿐 열기가 아니라 이 구분이 필요하다 (2026-08-04).
    ap.add_argument("--double", action="store_true")
    # 체크박스 클릭 전후 ToggleState를 비교해 실제로 바뀌었는지 검증한다
    # (2026-08-04, verified_toggle_click 참고 — CheckBox controlType 전반의
    # 잠재적 거짓 PASS 구멍을 막는다).
    ap.add_argument("--verify-toggle", action="store_true")
    args = ap.parse_args()

    enable_per_monitor_dpi()
    comtypes.CoInitialize()
    mod = comtypes.client.GetModule("UIAutomationCore.dll")
    uia = comtypes.client.CreateObject(
        "{ff48dba4-60ef-4201-aa87-54103eef594e}", interface=mod.IUIAutomation
    )

    main_h = args.hwnd
    if not main_h:
        print("osScopedInvoke: --hwnd is required", file=sys.stderr)
        sys.exit(2)
    # 2026-07-24: osExpandCollapse.py와 동일한 방어 — 방금 그 hwnd에
    # WinAppDriver scoped session이 막 생성된 직후 이 프로세스의 별도
    # IUIAutomation COM 클라이언트가 ElementFromHandle()을 호출하면 간헐적으로
    # COMError(-2147220991, 이벤트 구독자 관련)가 실측됨 — 셀렉터/로직 문제가
    # 아니라 타이밍 레이스이므로 실패 단정 전에 짧게 재시도한다.
    root = None
    for attempt in range(4):
        if attempt > 0:
            time.sleep(0.3)
        try:
            root = uia.ElementFromHandle(main_h)
        except Exception:
            root = None
        if root:
            break
    if not root:
        print("osScopedInvoke: ElementFromHandle failed", file=sys.stderr)
        sys.exit(2)

    win_top = None
    try:
        r = wintypes.RECT()
        if user32.GetWindowRect(main_h, ctypes.byref(r)):
            win_top = r.top
    except Exception:
        pass

    sel = json.loads(base64.b64decode(args.sel_b64).decode("utf-8"))
    item_cond = resolve_cond(uia, sel)
    if item_cond is None:
        print("osScopedInvoke: selector has no usable fields", file=sys.stderr)
        sys.exit(2)

    # 트리거(버튼 등)가 있으면 이 실행 안에서 먼저 클릭 — 별도 프로세스로
    # 쪼개 두 번 호출하지 않아 트리거-검색 사이의 지연(및 그로 인한 드롭다운
    # 자동-닫힘)을 없앤다.
    if args.trigger_sel_b64:
        trigger_sel = json.loads(base64.b64decode(args.trigger_sel_b64).decode("utf-8"))
        trigger_cond = resolve_cond(uia, trigger_sel)
        if trigger_cond is not None:
            trigger = pick_trigger(uia, root, trigger_cond, win_top, args.trigger_rel_y)
            if trigger:
                invoke_item(uia, mod, trigger)
            else:
                # 트리거를 못 찾으면 드롭다운이 아예 안 열려 이후 아이템
                # 검색이 원인불명으로 실패하는 것처럼 보인다 — 눈에 보이게
                # 남긴다 (2026-07-14, 침묵 스킵이 진단을 어렵게 만든 것을 확인).
                print(f"[osScopedInvoke] WARN trigger not found (sel={args.trigger_sel_b64}) — dropdown likely never opened")

    # --text-b64가 있으면 클릭/Invoke 대신 타이핑(ValuePattern.SetValue) —
    # osScopedType() JS wrapper 전용 (2026-07-17, owned 다이얼로그 안 Edit
    # 컨트롤에 타이핑하기 위해 도입 — Root 세션 REST 폴백의 15~20초 고정
    # 비용을 피한다. 검색 로직((a)(b) 둘 다)은 클릭과 완전히 동일).
    act = (lambda el: type_item(uia, mod, el, base64.b64decode(args.text_b64).decode("utf-8")))         if args.text_b64 else (
            (lambda el: verified_toggle_click(uia, mod, el, "osScopedInvoke", args.double))
            if args.verify_toggle else (lambda el: invoke_item(uia, mod, el, args.double))
        )
    verb = 'typed into' if args.text_b64 else 'invoked'

    # 최대 4회 시도(즉시 1회 + 300ms 간격 재시도 3회, 총 최대 ~0.9초) — 2026-07-17
    # 실측: "새 사이트(N)" 클릭 직후 뜨는 인라인 이름변경 상자(automationId="1")를
    # 즉시 1회만 찾으면 렌더링 레이스로 못 찾는 경우가 실제 GUI에서 재현됨
    # (FileZilla Site Manager). 기존 REST 경로(_findScoped)는 1초 간격으로 최대
    # 8초 폴링해 이런 레이스를 자연히 흡수했는데, COM 경로는 단발 시도라 그
    # 여유가 없었다 — _step()의 범용 Fail-and-Recover(ESC)에 기대면 이름변경
    # 상자에서 ESC가 변경 자체를 취소시켜 재시도도 함께 실패하므로(esc-recovery
    # 후 osScopedType 재실패로 실측 확인), 스크립트 자체에 짧은 재시도를 둔다.
    # 2026-07-24 실측: FileZilla 이름변경 상자는 "새 사이트(N)" 클릭 후
    # **2260ms**만에 나타났다(poc/diag_filezilla_rename.py) — 기존 예산
    # 4회(~0.9초)로는 구조적으로 못 잡는다. 타이핑은 20회(~6초)까지 기다리고
    # (REST 경로 _findScoped의 8초 폴링보다는 짧게), 클릭은 10회(~2.7초)로
    # 둔다. _step()의 ESC 복구는 이 대상에서 오히려 해로워 이제 건너뛰므로
    # (_escWouldHarm) 재시도 여유는 이 스크립트 안에서 확보해야 한다.
    attempts = 20 if args.text_b64 else 10
    main_pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(main_h, ctypes.byref(main_pid))
    owner_title = (base64.b64decode(args.owner_title_b64).decode("utf-8")
                   if args.owner_title_b64 else "")
    for attempt in range(attempts):
        if attempt > 0:
            time.sleep(0.3)

        # (a0) 이 이벤트가 녹화된 창이 메인 창이 아니라면 거기서 먼저 찾는다.
        #      약한 셀렉터(이름 단독)가 메인 창의 엉뚱한 동명 컨트롤에 먼저
        #      걸리는 것을 막는다 — owner_window_for() 주석의 PuTTY 닫기 사례.
        owner_h = owner_window_for(main_h, main_pid.value, owner_title)
        if owner_h:
            try:
                owner_root = uia.ElementFromHandle(owner_h)
                if owner_root:
                    item = owner_root.FindFirst(TreeScope_Subtree, item_cond)
                    if item and act(item):
                        print(f"[osScopedInvoke] {verb} under the window it was "
                              f"recorded in ({owner_title!r} hwnd={owner_h})")
                        sys.exit(0)
            except Exception:
                pass

        # (a) 메인 창 서브트리. Subtree = 창 자기 자신(root)도 포함해 검색한다 —
        #     Descendants만 쓰면 캡처된 타겟이 창 자체(예: className="#32770")인
        #     경우 구조적으로 못 찾는다(2026-07-16 FileZilla 다이얼로그 클릭 확인).
        # rel_y 힌트가 있으면 FindAll로 모든 후보를 모아 위치로 가려낸다
        # (2026-07-29, HeidiSQL: 같은 창 안에 automationId="DropDown"을
        # 공유하는 콤보가 둘 이상 — FindFirst는 항상 같은 하나만 반환).
        # 힌트가 없으면 기존과 동일하게 FindFirst 한 건만 본다.
        item = None
        try:
            if args.rel_y is not None:
                cands = elements_from(root.FindAll(TreeScope_Subtree, item_cond))
                if len(cands) > 1:
                    picked = pick_by_position(cands, win_top, args.rel_y)
                    print(f"[osScopedInvoke] selector matched {len(cands)} elements in main "
                          f"window — disambiguated by recorded position")
                    item = picked
                elif cands:
                    item = cands[0]
            else:
                item = root.FindFirst(TreeScope_Subtree, item_cond)
        except Exception:
            item = None
        if item and act(item):
            print(f"[osScopedInvoke] {verb} under main window subtree")
            sys.exit(0)

        # (b) 메인 창과 같은 프로세스(PID)가 소유한 다른 최상위 창 서브트리 —
        #     이미 열려 있는 팝업/드롭다운(예: PuTTY의 ComboLBox, FileZilla
        #     메뉴)을 잡는다. 새로 뜬 창인지 여부는 따지지 않는다(트리거가
        #     이미 직전 스텝에서 실행됐으므로 baseline diff 불필요). PID로
        #     반드시 한정한다 — PID 무관하게 데스크톱 전체를 뒤지면 완전히
        #     남남인 창을 잘못 클릭할 수 있음을 실측으로 확인(2026-07-15:
        #     7-Zip에서 "hansung"/"project" 등 사용자의 실제 폴더명을 검색하다가
        #     (a)에서 못 찾자 사용자가 실제로 열어둔 탐색기 창(explorer.exe,
        #     class=CabinetWClass)과 VS Code 창(Code.exe)에서 우연히 같은
        #     이름을 찾아 그 창을 대신 클릭 — 거짓 성공으로 로그에 "invoked"
        #     찍힘 + 사용자 창에 실제 부작용).
        for h in top_windows():
            if h == main_h:
                continue
            cand_pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(h, ctypes.byref(cand_pid))
            if cand_pid.value != main_pid.value:
                continue
            try:
                other_root = uia.ElementFromHandle(h)
                if not other_root:
                    continue
                item = other_root.FindFirst(TreeScope_Subtree, item_cond)
                if item and act(item):
                    print(f"[osScopedInvoke] {verb} under other top-level window hwnd={h}")
                    sys.exit(0)
            except Exception:
                continue

    # 마지막 수단(타이핑 전용): 포커스를 가진 입력 컨트롤. 인라인 이름변경
    # 상자처럼 automationId가 런타임에 변하는 대상은 셀렉터로는 영원히 못
    # 찾지만 포커스는 항상 갖고 있다. 클릭에는 적용하지 않는다 — "포커스된
    # 무언가를 대신 클릭"은 엉뚱한 동작을 조용히 수행할 위험이 크다.
    if args.text_b64:
        el = focused_input(uia, main_pid.value)
        if el and act(el):
            print(f"[osScopedInvoke] {verb} the focused input control")
            sys.exit(0)

    print(f"osScopedInvoke: target not found under main window or any other top-level window (sel={args.sel_b64})", file=sys.stderr)
    sys.exit(2)


if __name__ == "__main__":
    main()
