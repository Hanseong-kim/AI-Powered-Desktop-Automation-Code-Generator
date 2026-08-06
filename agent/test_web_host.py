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

    class _FakeRec:
        """Only what the repair actually reads, plus the real helper it calls."""
        target_hwnds = {6885104}
        _adopt_dead_row_cell = Recorder._adopt_dead_row_cell

    def restore(picked_by, raw, late, x, y, root_hwnd=6885104):
        """Run the repair and hand back the (possibly) fixed element dict."""
        info = dict(late)
        ins = _FakeIns({"picked_by": picked_by, "raw_info": raw,
                        "root_hwnd": root_hwnd})
        Recorder._restore_recycled_row_name(_FakeRec(), ins, info, x, y)
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

    # #7: the element DIED before the second read (every field failed, rect came
    # back as an ERR string). raw is the unlabeled Edit name-cell surrogate, so
    # the row-ancestor climb element_at() would have done is applied to the last
    # good observation. Numbers are the live 2026-08-06 capture.
    _DEAD_RECT = "ERR:COMError:(-2147220991, '이벤트에서 가입자를 불러낼 수 없습니다.', ...)"
    got = restore("smallest_element_at",
                  {"name": "hansung", "rect": (1000, 406, 1087, 430),
                   "automationId": "", "controlType": "Edit", "className": ""},
                  {"name": "", "rect": _DEAD_RECT, "automationId": "", "controlType": ""},
                  1050, 418)
    check("dead row cell: recovers 'hansung' from the pre-death read",
          got["name"] == "hansung")
    check("dead row cell: records it as the ListItem row, not the Edit cell",
          got["controlType"] == "ListItem")
    check("dead row cell: replaces the ERR rect with the real one",
          got["rect"] == (1000, 406, 1087, 430))

    # ...but only for the narrow shape. A large named element under the point is
    # the 'adopted the backdrop instead of the control' failure (FileZilla log
    # pane, 2026-08-05) — must stay dropped.
    got = restore("smallest_element_at",
                  {"name": "RichEdit Control", "rect": (994, 306, 1911, 982),
                   "automationId": "", "controlType": "Edit", "className": ""},
                  {"name": "", "rect": _DEAD_RECT, "automationId": "", "controlType": ""},
                  1050, 418)
    check("dead element: a window-filling backdrop is NOT adopted", got["name"] == "")

    got = restore("smallest_element_at",
                  {"name": "hansung", "rect": (1000, 406, 1087, 430),
                   "automationId": "", "controlType": "Edit", "className": ""},
                  {"name": "", "rect": _DEAD_RECT, "automationId": "", "controlType": ""},
                  1050, 418, root_hwnd=66794)   # Chrome, not a tracked window
    check("dead element: an untracked window is NOT adopted", got["name"] == "")

    got = restore("smallest_element_at",
                  {"name": "Save", "rect": (1000, 406, 1087, 430),
                   "automationId": "btnSave", "controlType": "Button", "className": ""},
                  {"name": "", "rect": _DEAD_RECT, "automationId": "", "controlType": ""},
                  1050, 418)
    check("dead element: a normal named control is NOT relabelled as a row",
          got["name"] == "" and got["controlType"] == "")

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
