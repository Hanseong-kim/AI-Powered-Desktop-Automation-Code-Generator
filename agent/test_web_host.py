"""Inline harness — agent.py has no test suite; this file is run directly."""
import ctypes
import sys

sys.path.insert(0, ".")
from agent import (   # noqa: E402
    is_chromium_host_class, is_web_host, smallest_rect_index, UIAInspector,
    Recorder,
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

    # ── _restore_recycled_row_name ─────────────────────────────────────────
    # Fixtures are the REAL numbers from the 7-Zip capture of 2026-08-06
    # (recorded-events/SevenZip_2026-08-06T03-40-50-210Z.json + the agent
    # console [trace]/[diag-click] lines for the same events).
    class _FakeIns:
        def __init__(self, trace):
            self._last_trace = trace

    def restore(picked_by, raw, late, x, y):
        """Run the repair and hand back the (possibly) fixed element dict."""
        info = dict(late)
        Recorder._restore_recycled_row_name(
            _FakeIns({"picked_by": picked_by, "raw_info": raw}), info, x, y)
        return info

    # #4: user double-clicked 'C:'; the late read had already slid onto the
    # first row of the folder that click opened.
    got = restore("row-ancestor",
                  {"name": "C:", "rect": (1000, 334, 1041, 358), "automationId": ""},
                  {"name": "$Recycle.Bin", "rect": (996, 334, 1756, 358), "automationId": ""},
                  1038, 344)
    check("recycled row: restores 'C:' over the post-navigation '$Recycle.Bin'",
          got["name"] == "C:")
    check("recycled row: keeps the climbed ROW's rect, not the raw cell's",
          got["rect"] == (996, 334, 1756, 358))

    # #10: same shape, different folder.
    got = restore("row-ancestor",
                  {"name": "project", "rect": (1000, 358, 1075, 382), "automationId": ""},
                  {"name": ".code-review-graph", "rect": (996, 358, 1756, 382), "automationId": ""},
                  1045, 376)
    check("recycled row: restores 'project' over '.code-review-graph'",
          got["name"] == "project")

    # #1: nothing was recycled (both reads agree) — must be a no-op. This is
    # the case that covers every well-behaved capture in every other app.
    got = restore("row-ancestor",
                  {"name": "컴퓨터", "rect": (1000, 334, 1073, 358), "automationId": ""},
                  {"name": "컴퓨터", "rect": (996, 334, 1156, 358), "automationId": ""},
                  1047, 347)
    check("no rot (1st read == 2nd read) leaves the element untouched",
          got["name"] == "컴퓨터")

    # #7: the late read degenerated to the List container. Not repaired on
    # purpose — restoring a name there would build a chimera.
    got = restore("smallest_element_at",
                  {"name": "hansung", "rect": (1000, 406, 1087, 430), "automationId": ""},
                  {"name": "", "rect": (994, 306, 1911, 982), "automationId": "1001"},
                  1055, 417)
    check("a non-row-ancestor pick is left alone (no chimera)", got["name"] == "")

    # Guards.
    got = restore("row-ancestor",
                  {"name": "C:", "rect": (1000, 334, 1041, 358), "automationId": ""},
                  {"name": "$Recycle.Bin", "rect": (996, 334, 1756, 358), "automationId": ""},
                  1500, 344)   # click outside the raw cell
    check("click point outside the raw rect blocks the restore",
          got["name"] == "$Recycle.Bin")

    got = restore("row-ancestor",
                  {"name": "Elsewhere", "rect": (10, 10, 40, 30), "automationId": ""},
                  {"name": "$Recycle.Bin", "rect": (996, 334, 1756, 358), "automationId": ""},
                  20, 20)      # raw is not a cell OF this row
    check("a raw rect outside the adopted row blocks the restore",
          got["name"] == "$Recycle.Bin")

    got = restore("row-ancestor",
                  {"name": "", "rect": (1000, 334, 1041, 358), "automationId": ""},
                  {"name": "$Recycle.Bin", "rect": (996, 334, 1756, 358), "automationId": ""},
                  1038, 344)   # raw had no name to restore
    check("a nameless raw read blocks the restore",
          got["name"] == "$Recycle.Bin")

    print(f"\n{len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
