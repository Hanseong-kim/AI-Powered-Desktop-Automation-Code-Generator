import sys, json, base64, argparse, ctypes, time
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
TreeScope_Descendants = 4
ExpandCollapseState_Expanded = 1

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


def invoke_item(mod, el):
    try:
        el.SetFocus()
    except Exception:
        pass
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
        if not item_name and invoke_item(mod, target):
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
        if not item_name and invoke_item(mod, target):
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
    if item and invoke_item(mod, item):
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
            if item and invoke_item(mod, item):
                print(f"[osExpandCollapse] invoked '{item_name}' under new popup hwnd={h}")
                sys.exit(0)
        except Exception:
            continue

    print(f"osExpandCollapse: item '{item_name}' not found under main window or any new popup window", file=sys.stderr)
    sys.exit(2)


if __name__ == "__main__":
    main()
