"""Inline harness — agent.py has no test suite; this file is run directly."""
import ctypes
import sys

sys.path.insert(0, ".")
from agent import (   # noqa: E402
    is_chromium_host_class, is_web_host, smallest_rect_index, UIAInspector,
)

failures = []


def check(label, ok):
    print(("PASS  " if ok else "FAIL  ") + label)
    if not ok:
        failures.append(label)


def main():
    check("Chrome_WidgetWin_1 is recognised",
          is_chromium_host_class("Chrome_WidgetWin_1") is True)
    check("Chrome_RenderWidgetHostHWND is recognised",
          is_chromium_host_class("Chrome_RenderWidgetHostHWND") is True)
    check("TV_WebView2Control is recognised",
          is_chromium_host_class("TV_WebView2Control") is True)
    check("a native class is not recognised",
          is_chromium_host_class("Notepad") is False)
    check("an empty class name is not recognised",
          is_chromium_host_class("") is False)
    # hwnd 0 is never a window -> must not raise, must be False
    check("is_web_host(0) is False", is_web_host(0) is False)

    ins = UIAInspector()
    root = ins._uia.ElementFromHandle(ctypes.windll.user32.GetDesktopWindow())
    n = ins.settled_subtree_count(root, timeout=2.0, quiet_for=0.3)
    check("settled_subtree_count returns a positive int for the desktop",
          isinstance(n, int) and n > 0)

    #        rect list                              point       expected index
    cases = [
        ([(0, 0, 100, 100), (10, 10, 20, 20)],      (15, 15),   1),   # inner wins
        ([(10, 10, 20, 20), (0, 0, 100, 100)],      (15, 15),   0),   # order-independent
        ([(0, 0, 100, 100)],                        (15, 15),   0),
        ([(0, 0, 100, 100), (10, 10, 20, 20)],      (99, 99),   0),   # only outer contains
        ([(0, 0, 10, 10)],                          (10, 10),   None),  # half-open: bottom/right excluded
        ([],                                        (1, 1),     None),
    ]
    for rects, (px, py), want in cases:
        got = smallest_rect_index(rects, px, py)
        check(f"smallest_rect_index({rects}, {px},{py}) == {want}", got == want)

    print(f"\n{len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
