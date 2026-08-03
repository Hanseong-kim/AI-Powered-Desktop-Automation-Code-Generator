"""Inline harness — agent.py has no test suite; this file is run directly."""
import sys

sys.path.insert(0, ".")
from agent import is_chromium_host_class, is_web_host   # noqa: E402

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
    print(f"\n{len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
