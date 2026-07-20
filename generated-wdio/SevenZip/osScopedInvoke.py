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
UIA_InvokePatternId = 10000
UIA_SelectionItemPatternId = 10010
TreeScope_Descendants = 4

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
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hwnd", type=int, required=True)
    ap.add_argument("--sel-b64", required=True)
    ap.add_argument("--trigger-sel-b64", default=None)
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
                trigger = root.FindFirst(TreeScope_Descendants, trigger_cond)
            except Exception:
                trigger = None
            if trigger:
                invoke_item(mod, trigger)
            else:
                # 트리거를 못 찾으면 드롭다운이 아예 안 열려 이후 아이템
                # 검색이 원인불명으로 실패하는 것처럼 보인다 — 눈에 보이게
                # 남긴다 (2026-07-14, 침묵 스킵이 진단을 어렵게 만든 것을 확인).
                print(f"[osScopedInvoke] WARN trigger not found (sel={args.trigger_sel_b64}) — dropdown likely never opened")

    # (a) 메인 창 서브트리.
    try:
        item = root.FindFirst(TreeScope_Descendants, item_cond)
    except Exception:
        item = None
    if item and invoke_item(mod, item):
        print("[osScopedInvoke] invoked under main window subtree")
        sys.exit(0)

    # (b) 메인 창과 같은 프로세스(PID)가 소유한 다른 최상위 창 서브트리 — 이미
    #     열려 있는 팝업/드롭다운(예: PuTTY의 ComboLBox, FileZilla 메뉴)을
    #     잡는다. 새로 뜬 창인지 여부는 따지지 않는다(트리거가 이미 직전
    #     스텝에서 실행됐으므로 baseline diff 불필요). PID로 반드시 한정한다 —
    #     PID 무관하게 데스크톱 전체를 뒤지면 완전히 남남인 창을 잘못 클릭할
    #     수 있음을 실측으로 확인(2026-07-15: 7-Zip에서 "hansung"/"project" 등
    #     사용자의 실제 폴더명을 검색하다가 (a)에서 못 찾자 사용자가 실제로
    #     열어둔 탐색기 창(explorer.exe, class=CabinetWClass)과 VS Code
    #     창(Code.exe)에서 우연히 같은 이름을 찾아 그 창을 대신 클릭 — 거짓
    #     성공으로 로그에 "invoked" 찍힘 + 사용자 창에 실제 부작용).
    main_pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(main_h, ctypes.byref(main_pid))
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
            item = other_root.FindFirst(TreeScope_Descendants, item_cond)
            if item and invoke_item(mod, item):
                print(f"[osScopedInvoke] invoked under other top-level window hwnd={h}")
                sys.exit(0)
        except Exception:
            continue

    print(f"osScopedInvoke: target not found under main window or any other top-level window (sel={args.sel_b64})", file=sys.stderr)
    sys.exit(2)


if __name__ == "__main__":
    main()
