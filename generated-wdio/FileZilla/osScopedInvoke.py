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
UIA_ControlTypeProperty = 30003
UIA_InvokePatternId = 10000
UIA_SelectionItemPatternId = 10010
UIA_ValuePatternId = 10002
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


def top_windows():
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            found.append(hwnd)
        return True

    user32.EnumWindows(cb, 0)
    return found


def resolve_cond(uia, sel):
    conds = []
    if sel.get("automationId"):
        conds.append(uia.CreatePropertyCondition(UIA_AutomationIdProperty, sel["automationId"]))
    if sel.get("name"):
        conds.append(uia.CreatePropertyCondition(UIA_NameProperty, sel["name"]))
    if sel.get("className"):
        conds.append(uia.CreatePropertyCondition(UIA_ClassNameProperty, sel["className"]))
    if not conds:
        return None
    cond = conds[0]
    for c in conds[1:]:
        cond = uia.CreateAndCondition(cond, c)
    return cond


def invoke_item(mod, el):
    focus_ok = False
    try:
        el.SetFocus()
        focus_ok = True
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
        ctrl_type = el.CurrentControlType
    except Exception:
        ctrl_type = None
    if focus_ok and ctrl_type in PASSIVE_CONTROL_TYPES:
        return True
    print(f"[osScopedInvoke] found element (controlType={ctrl_type}) but no actionable pattern (Invoke/Select) succeeded", file=sys.stderr)
    return False


# 2026-07-17: owned 다이얼로그(WAD가 scoped session을 거부하는 창)에 타이핑하기
# 위한 COM 경로. 기존에는 getWindowSession()이 owned 창을 만나면 WinAppDriver
# Root 세션 REST로 전체 데스크톱 XPath 검색을 폴백으로 썼는데, 실측(2026-07-17
# FileZilla 다이얼로그 진단): 이 Root-세션 REST 호출은 쿼리 내용/매치 여부와
# 무관하게 매번 15~20초가 걸린다(빈 결과조차 15.6초) — WinAppDriver 3.5.2의
# Root 세션 자체가 모든 element 조회에 고정 비용을 갖는 것으로 보임. hwnd는
# 이미 EnumWindows로 알고 있으므로, 같은 COM 스택(osScopedInvoke의 클릭 경로와
# 동일)으로 즉시 타이핑하면 이 15~20초를 완전히 우회한다.
def type_item(mod, el, text):
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
    args = ap.parse_args()

    comtypes.CoInitialize()
    mod = comtypes.client.GetModule("UIAutomationCore.dll")
    uia = comtypes.client.CreateObject(
        "{ff48dba4-60ef-4201-aa87-54103eef594e}", interface=mod.IUIAutomation
    )

    main_h = args.hwnd
    if not main_h:
        print("osScopedInvoke: --hwnd is required", file=sys.stderr)
        sys.exit(2)
    root = uia.ElementFromHandle(main_h)
    if not root:
        print("osScopedInvoke: ElementFromHandle failed", file=sys.stderr)
        sys.exit(2)

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
            trigger = None
            try:
                trigger = root.FindFirst(TreeScope_Subtree, trigger_cond)
            except Exception:
                trigger = None
            if trigger:
                invoke_item(mod, trigger)
            else:
                # 트리거를 못 찾으면 드롭다운이 아예 안 열려 이후 아이템
                # 검색이 원인불명으로 실패하는 것처럼 보인다 — 눈에 보이게
                # 남긴다 (2026-07-14, 침묵 스킵이 진단을 어렵게 만든 것을 확인).
                print(f"[osScopedInvoke] WARN trigger not found (sel={args.trigger_sel_b64}) — dropdown likely never opened")

    # --text-b64가 있으면 클릭/Invoke 대신 타이핑(ValuePattern.SetValue) —
    # osScopedType() JS wrapper 전용 (2026-07-17, owned 다이얼로그 안 Edit
    # 컨트롤에 타이핑하기 위해 도입 — Root 세션 REST 폴백의 15~20초 고정
    # 비용을 피한다. 검색 로직((a)(b) 둘 다)은 클릭과 완전히 동일).
    act = (lambda el: type_item(mod, el, base64.b64decode(args.text_b64).decode("utf-8")))         if args.text_b64 else (lambda el: invoke_item(mod, el))
    verb = 'typed into' if args.text_b64 else 'invoked'

    # 최대 4회 시도(즉시 1회 + 300ms 간격 재시도 3회, 총 최대 ~0.9초) — 2026-07-17
    # 실측: "새 사이트(N)" 클릭 직후 뜨는 인라인 이름변경 상자(automationId="1")를
    # 즉시 1회만 찾으면 렌더링 레이스로 못 찾는 경우가 실제 GUI에서 재현됨
    # (FileZilla Site Manager). 기존 REST 경로(_findScoped)는 1초 간격으로 최대
    # 8초 폴링해 이런 레이스를 자연히 흡수했는데, COM 경로는 단발 시도라 그
    # 여유가 없었다 — _step()의 범용 Fail-and-Recover(ESC)에 기대면 이름변경
    # 상자에서 ESC가 변경 자체를 취소시켜 재시도도 함께 실패하므로(esc-recovery
    # 후 osScopedType 재실패로 실측 확인), 스크립트 자체에 짧은 재시도를 둔다.
    main_pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(main_h, ctypes.byref(main_pid))
    for attempt in range(4):
        if attempt > 0:
            time.sleep(0.3)

        # (a) 메인 창 서브트리. Subtree = 창 자기 자신(root)도 포함해 검색한다 —
        #     Descendants만 쓰면 캡처된 타겟이 창 자체(예: className="#32770")인
        #     경우 구조적으로 못 찾는다(2026-07-16 FileZilla 다이얼로그 클릭 확인).
        try:
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

    print(f"osScopedInvoke: target not found under main window or any other top-level window (sel={args.sel_b64})", file=sys.stderr)
    sys.exit(2)


if __name__ == "__main__":
    main()
