"""Diagnostic (2026-07-13) -- does ExpandCollapsePattern actually open
PuTTY's "Proxy type:" ComboBox dropdown / FileZilla's "File" menu / PuTTY's
tree-category +/- toggle, and are the resulting children reachable afterward?

Standalone, admin not required, COM IUIAutomation (comtypes) -- same stack as
agent.py and poc3_dialog_e2e.py. Attaches to ALREADY-RUNNING putty.exe /
filezilla.exe instances (does not launch them) so it can be re-run freely
against whatever state the apps are currently in.

Run: python poc/diag_expandcollapse.py
"""
import sys
import time

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import comtypes
import comtypes.client

UIA_ControlTypeProperty = 30003
UIA_NameProperty = 30005
UIA_AutomationIdProperty = 30011
UIA_ExpandCollapsePatternId = 10005
CT_ComboBox = 50003
CT_MenuItem = 50011
CT_TreeItem = 50024
TreeScope_Descendants = 4
TreeScope_Subtree = 7


def get_uia():
    comtypes.CoInitialize()
    mod = comtypes.client.GetModule("UIAutomationCore.dll")
    uia = comtypes.client.CreateObject(
        "{ff48dba4-60ef-4201-aa87-54103eef594e}", interface=mod.IUIAutomation
    )
    return uia, mod


def top_window_for_process(uia, mod, proc_name_substr):
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32

    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            buf = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(hwnd, buf, 256)
            found.append((hwnd, buf.value))
        return True

    user32.EnumWindows(cb, 0)
    for hwnd, title in found:
        if proc_name_substr.lower() in title.lower():
            return hwnd, title
    return None, None


def try_expand_collapse(uia, mod, elem, label):
    """Returns (supported: bool, state_before, state_after, error)"""
    pat = None
    try:
        pat = elem.GetCurrentPattern(UIA_ExpandCollapsePatternId)
    except Exception as e:
        return False, None, None, f"GetCurrentPattern failed: {e}"
    if not pat:
        return False, None, None, "pattern not available"
    ecp = pat.QueryInterface(mod.IUIAutomationExpandCollapsePattern)
    before = ecp.CurrentExpandCollapseState
    print(f"[{label}] ExpandCollapsePattern SUPPORTED, state before = {before} "
          f"(0=Collapsed 1=Expanded 2=PartiallyExpanded 3=LeafNode)")
    try:
        ecp.Expand()
    except Exception as e:
        return True, before, None, f"Expand() call failed: {e}"
    time.sleep(0.6)
    after = ecp.CurrentExpandCollapseState
    print(f"[{label}] state after Expand() = {after}")
    return True, before, after, None


def find_by_automation_id(uia, root, aid):
    cond = uia.CreatePropertyCondition(UIA_AutomationIdProperty, aid)
    return root.FindFirst(TreeScope_Subtree, cond)


def find_by_name_and_type(uia, root, name, ctrl_type, scope=TreeScope_Subtree):
    cond = uia.CreateAndCondition(
        uia.CreatePropertyCondition(UIA_NameProperty, name),
        uia.CreatePropertyCondition(UIA_ControlTypeProperty, ctrl_type),
    )
    return root.FindFirst(scope, cond)


def diag_putty(uia, mod):
    print("\n=== PuTTY: 'Proxy type:' ComboBox ===")
    hwnd, title = top_window_for_process(uia, mod, "PuTTY Configuration")
    if not hwnd:
        print("PuTTY Configuration window not found -- skipping")
        return
    print(f"window hwnd={hwnd:#x} title={title!r}")
    root = uia.ElementFromHandle(hwnd)

    combo = find_by_automation_id(uia, root, "1044")
    if not combo:
        print("ComboBox AutomationId=1044 ('Proxy type:') not found in current "
              "tree state -- navigate PuTTY to Connection > Proxy first, then rerun")
        return
    print(f"found ComboBox: Name={combo.CurrentName!r} ClassName={combo.CurrentClassName!r}")

    supported, before, after, err = try_expand_collapse(uia, mod, combo, "ComboBox 1044")
    if not supported:
        print(f"RESULT: ExpandCollapsePattern NOT supported on this ComboBox ({err})")
    elif err:
        print(f"RESULT: pattern supported but Expand() errored: {err}")
    else:
        changed = before != after
        print(f"RESULT: Expand() {'changed' if changed else 'did NOT change'} state "
              f"({before} -> {after})")
        # Now check reachability of a dropdown item from THREE scopes:
        # (a) descendants of the combobox element itself
        # (b) descendants of the dialog root (what a WinAppDriver session scoped
        #     to this window would see)
        item_cond = uia.CreatePropertyCondition(UIA_NameProperty, "SOCKS 5")
        under_combo = combo.FindFirst(TreeScope_Descendants, item_cond)
        under_root = root.FindFirst(TreeScope_Descendants, item_cond)
        print(f"[reachability] 'SOCKS 5' ListItem found under ComboBox subtree: "
              f"{'YES' if under_combo else 'NO'}")
        print(f"[reachability] 'SOCKS 5' ListItem found under DIALOG ROOT subtree "
              f"(== what a session scoped to this window would see): "
              f"{'YES' if under_root else 'NO'}")
        # cleanup: collapse back so PuTTY UI is left as found
        try:
            pat = combo.GetCurrentPattern(UIA_ExpandCollapsePatternId)
            ecp = pat.QueryInterface(mod.IUIAutomationExpandCollapsePattern)
            ecp.Collapse()
        except Exception:
            pass

    print("\n=== PuTTY: TreeItem +/- toggle (Category tree) ===")
    tree_item = find_by_name_and_type(uia, root, "Window", CT_TreeItem)
    if not tree_item:
        print("TreeItem 'Window' not found -- skipping")
    else:
        print(f"found TreeItem: Name={tree_item.CurrentName!r}")
        supported, before, after, err = try_expand_collapse(uia, mod, tree_item, "TreeItem 'Window'")
        if not supported:
            print(f"RESULT: ExpandCollapsePattern NOT supported on this TreeItem ({err})")
        else:
            print(f"RESULT: TreeItem ExpandCollapsePattern supported "
                  f"({'state changed' if before != after else 'no change (' + str(err or '') + ')'})")
            # restore
            try:
                pat = tree_item.GetCurrentPattern(UIA_ExpandCollapsePatternId)
                ecp = pat.QueryInterface(mod.IUIAutomationExpandCollapsePattern)
                if ecp.CurrentExpandCollapseState != before:
                    if before == 0:
                        ecp.Collapse()
                    else:
                        ecp.Expand()
            except Exception:
                pass


def diag_filezilla(uia, mod):
    print("\n=== FileZilla: '파일(F)' menu bar MenuItem ===")
    hwnd, title = top_window_for_process(uia, mod, "FileZilla")
    if not hwnd:
        print("FileZilla window not found -- skipping")
        return
    print(f"window hwnd={hwnd:#x} title={title!r}")
    root = uia.ElementFromHandle(hwnd)

    menu_item = find_by_name_and_type(uia, root, "파일(F)", CT_MenuItem)
    if not menu_item:
        print("MenuItem '파일(F)' not found in current tree -- skipping")
        return
    print(f"found MenuItem: Name={menu_item.CurrentName!r}")
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32

    def snapshot_top_windows():
        found = []

        @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
        def cb(h, _):
            if user32.IsWindowVisible(h):
                found.append(h)
            return True

        user32.EnumWindows(cb, 0)
        return set(found)

    def win_class(h):
        buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(h, buf, 256)
        return buf.value

    baseline = snapshot_top_windows()
    supported, before, after, err = try_expand_collapse(uia, mod, menu_item, "MenuItem '파일(F)'")
    if not supported:
        print(f"RESULT: ExpandCollapsePattern NOT supported on this MenuItem ({err})")
        print("        (fallback candidate: Alt+F keyboard mnemonic -- keyboard "
              "injection, not coordinates, still compliant with §3)")
    else:
        changed = before != after
        print(f"RESULT: Expand() {'changed' if changed else 'did NOT change'} state "
              f"({before} -> {after})")
        # check whether a submenu item becomes reachable UNDER the MenuItem itself
        sub_cond = uia.CreatePropertyCondition(UIA_ControlTypeProperty, CT_MenuItem)
        subs = menu_item.FindAll(TreeScope_Descendants, sub_cond)
        print(f"[reachability] submenu MenuItem descendants under the MenuItem element: "
              f"{subs.Length}")

        # check for a NEW top-level popup window (native TrackPopupMenu creates a
        # separate #32768-class window -- the submenu items would live THERE,
        # not nested under the original menu-bar MenuItem element)
        time.sleep(0.3)
        after_windows = snapshot_top_windows()
        new_hwnds = after_windows - baseline
        if new_hwnds:
            for h in new_hwnds:
                cls = win_class(h)
                print(f"[popup-window] NEW top-level window hwnd={h:#x} class={cls!r}")
                try:
                    popup_root = uia.ElementFromHandle(h)
                    popup_subs = popup_root.FindAll(TreeScope_Descendants, sub_cond)
                    print(f"    MenuItem descendants under THIS popup window: {popup_subs.Length}")
                    for i in range(min(popup_subs.Length, 10)):
                        print(f"      - {popup_subs.GetElement(i).CurrentName!r}")
                except Exception as e:
                    print(f"    (failed to inspect popup window: {e})")
        else:
            print("[popup-window] NO new top-level window appeared after Expand() "
                  "-- menu items must be found some other way (re-check timing, or "
                  "the popup may be a child/owned window not caught by EnumWindows top-level scan)")

        try:
            pat = menu_item.GetCurrentPattern(UIA_ExpandCollapsePatternId)
            ecp = pat.QueryInterface(mod.IUIAutomationExpandCollapsePattern)
            ecp.Collapse()
        except Exception:
            pass


def main():
    uia, mod = get_uia()
    diag_putty(uia, mod)
    diag_filezilla(uia, mod)
    print("\nDone. Zero SetCursorPos/mouse_event/pixel coordinates used.")


if __name__ == "__main__":
    main()
