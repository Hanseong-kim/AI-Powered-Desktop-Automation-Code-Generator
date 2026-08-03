"""Inline harness — agent.py has no test suite; this file is run directly."""
import ctypes
import sys

sys.path.insert(0, ".")
from agent import CHROMIUM_HOST_CLASSES, is_web_host   # noqa: E402

failures = []


def check(label, ok):
    print(("PASS  " if ok else "FAIL  ") + label)
    if not ok:
        failures.append(label)


def main():
    check("Chrome_WidgetWin is a known host class",
          any(c.startswith("Chrome_WidgetWin") for c in CHROMIUM_HOST_CLASSES))
    check("TV_WebView2Control is a known host class",
          "TV_WebView2Control" in CHROMIUM_HOST_CLASSES)
    # hwnd 0 is never a window -> must not raise, must be False
    check("is_web_host(0) is False", is_web_host(0) is False)
    # The desktop window has no Chromium child
    desktop = ctypes.windll.user32.GetDesktopWindow()
    check("is_web_host(desktop) is False", is_web_host(desktop) is False)
    print(f"\n{len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
