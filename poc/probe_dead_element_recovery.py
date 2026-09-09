r"""
================================================================================
  Dead-Element Recovery Probe
  A click that destroys its own window must still be captured as that control.
================================================================================

WHAT THIS IS FOR

    Buttons that close their own window (CLOSE, CANCEL, 닫기, 취소) are the one
    control family the capture pipeline structurally races: the worker thread
    inspects 0.1-0.6s after the button went down, and by then the UIA provider
    is gone. element_at()'s FIRST read caught the element alive, and _inspect()
    has a dead-element recovery that adopts exactly that observation.

    Measured 2026-09-01 (Medflow HTA), one recording, two outcomes:

        닫기   BoundingRectangle returned (0, 0, 0, 0)
               -> recovery ran      -> "#1 click name='닫기' [name]"        OK
        CLOSE  BoundingRectangle RAISED (describe stores "ERR:COMError:...")
               raw=[name='CLOSE' ct='Button' id='closeBtn' rect=(1491,675,1603,709)]
               -> recovery never ran -> "#14 click id='' name='' [coordinate]"

    The recovery is gated behind `light_dismiss`, and the branch that sets
    light_dismiss was itself guarded on `isinstance(rect, tuple)` — so the
    raise-variant skipped the whole thing while holding the correct answer.
    Whether a dying provider raises or returns zeros is arbitrary; both mean
    dead. CANCEL (cancelBtn) was lost the same way in the same recording.

HOW TO RUN IT

    Self-contained: launches MedflowDetail.hta, invokes its CLOSE button
    through UIA (no synthetic mouse input), waits for the provider to die,
    then calls the REAL Recorder._inspect() at the button's centre and checks
    what comes back.

        python poc\probe_dead_element_recovery.py
        python poc\probe_dead_element_recovery.py --delay 0.5

    Run it against an app you launched yourself (integrity level must match —
    CLAUDE.md §4).

    Exit 0 = the dead control was recovered by identity (AutomationId/Name).
    Exit 1 = it came back blank/coordinate, i.e. the step is lost.
"""

import argparse
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                os.pardir, "agent"))

import agent as agentmod  # noqa: E402

HTA = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir,
                   "mock-app", "medflow-hta", "MedflowDetail.hta")
INVOKE_PATTERN_ID = 10000
GA_ROOT = 2


def top_level(uia):
    root = uia.GetRootElement()
    walker = uia.ControlViewWalker
    child = walker.GetFirstChildElement(root)
    out = []
    while child:
        try:
            out.append((str(child.CurrentName),
                        int(child.CurrentNativeWindowHandle), child))
        except Exception:
            pass
        child = walker.GetNextSiblingElement(child)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--aid", default="closeBtn",
                    help="AutomationId of the window-destroying button")
    ap.add_argument("--title", default="Medflow Patient Detail")
    ap.add_argument("--delay", type=float, default=0.25,
                    help="seconds to wait after Invoke() before inspecting — "
                         "models the worker thread's lag (measured 0.09-0.60s)")
    ap.add_argument("--race", type=float, default=None,
                    help="seconds after _inspect() starts at which to Invoke() "
                         "the button, from a timer — use this to land inside "
                         "the narrow 'hit test found it, then it died' regime")
    ap.add_argument("--inject-dead-rect", action="store_true",
                    help="deterministic alternative to --race: make the SECOND "
                         "describe() report the unreadable rect the live bug "
                         "produced, so the dead-element path runs on demand")
    ap.add_argument("--no-launch", action="store_true",
                    help="attach to an already-open window instead")
    args = ap.parse_args()

    agentmod._enable_per_monitor_dpi_awareness()

    proc = None
    if not args.no_launch:
        proc = subprocess.Popen(["mshta.exe", os.path.abspath(HTA)])
        time.sleep(4)

    ok = False
    try:
        ins = agentmod.UIAInspector()
        win = next((t for t in top_level(ins._uia)
                    if args.title.lower() in t[0].lower()), None)
        if win is None:
            print(f"!! no window titled like {args.title!r}")
            return 2
        print(f"[window] {win[0]!r} hwnd={win[1]}")

        cond = ins._uia.CreatePropertyCondition(30011, args.aid)
        btn = win[2].FindFirst(7, cond)
        if not btn:                      # NULL COM pointer, not None
            print(f"!! no AutomationId={args.aid!r} in that window")
            return 2
        info = ins.describe(btn)
        r = info.get("rect")
        cx, cy = (r[0] + r[2]) // 2, (r[1] + r[3]) // 2
        print(f"[target ] ct={info.get('controlType')} aid={info.get('automationId')!r} "
              f"name={info.get('name')!r} rect={r} centre=({cx},{cy})")

        # Those pixels must belong to OUR window. An elevated window covering
        # them makes ElementFromPoint raise E_ACCESSDENIED, which surfaces as
        # an empty trace and reads exactly like a code failure (measured
        # 2026-09-01 — a leftover elevated mshta from a recording session).
        import ctypes

        class PT(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

        u32 = ctypes.windll.user32
        u32.WindowFromPoint.argtypes = [PT]
        u32.WindowFromPoint.restype = ctypes.c_void_p
        u32.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        u32.GetAncestor.restype = ctypes.c_void_p
        # An elevated window cannot be pushed behind by this process, and
        # SetForegroundWindow is refused from a background process — but the
        # TOPMOST z-band is above every ordinary window regardless of
        # integrity level, so this reliably uncovers our own button.
        HWND_TOPMOST, SWP_NOSIZE, SWP_NOACTIVATE = -1, 0x0001, 0x0010
        u32.SetWindowPos(win[1], HWND_TOPMOST, 0, 0, 0, 0,
                         SWP_NOSIZE | SWP_NOACTIVATE)
        u32.SetForegroundWindow(win[1])
        time.sleep(0.4)
        h = u32.WindowFromPoint(PT(cx, cy))
        owner = (u32.GetAncestor(h, GA_ROOT) or h) if h else None
        if owner != win[1]:
            print(f"!! ({cx},{cy}) belongs to hwnd={owner}, not to the target "
                  f"window {win[1]} — another window covers the button. "
                  "Close it and retry; this is not a result.")
            return 2

        # A real Recorder, with this window tracked, exactly as a recording
        # session would have it.
        rec = agentmod.Recorder()
        rec.target_hwnds.add(win[1])
        rec._popup_hwnds.add(win[1])

        # Destroy the window the way the user's click does — through the
        # control itself, not by synthesising mouse input.
        def _kill():
            try:
                btn.GetCurrentPattern(INVOKE_PATTERN_ID).QueryInterface(
                    ins._mod.IUIAutomationInvokePattern).Invoke()
            except Exception:
                pass

        if args.inject_dead_rect:
            # Fault injection instead of a race. The regime under test is
            # "element_at()'s read succeeded, then the provider died before
            # _inspect()'s read" — a sub-millisecond window that could not be
            # hit from outside (three timing approaches tried, 2026-09-01:
            # invoke-then-sleep at 0-0.25s and a concurrent timer at
            # 1-20ms all overshot). Making the SECOND describe() report the
            # exact failure the live bug produced reproduces it exactly and
            # deterministically, and exercises the real _inspect() code path.
            real_describe = ins.describe
            real_element_at = ins.element_at
            state = {"dead": False}
            ERR = ("ERR:COMError:(-2147220991, "
                   "'이벤트에서 가입자를 불러낼 수 없습니다.', "
                   "(None, None, None, 0, None))")

            def _element_at(x, y):
                # Everything element_at() reads happens while the provider is
                # still alive — that is the whole premise of the recovery, and
                # it is what the real log shows (raw=[id='closeBtn'
                # name='CLOSE' rect=(1491,675,1603,709)]). Only the read
                # _inspect() makes AFTERWARDS finds it gone. Blanking by call
                # COUNT instead got this wrong: element_at()'s own
                # smallest_info snapshot is call #2, so killing from #2 onward
                # destroyed the very snapshot the recovery needs.
                try:
                    return real_element_at(x, y)
                finally:
                    state["dead"] = True

            def _describe_dying(el, *a, **kw):
                d = dict(real_describe(el, *a, **kw))
                if state["dead"]:
                    for k in ("name", "automationId", "className", "controlType"):
                        d[k] = ""
                    d["rect"] = ERR
                return d

            ins.element_at = _element_at
            ins.describe = _describe_dying
            print("[act    ] injecting a dead second read (rect -> ERR string), "
                  "window left open")
        elif args.race is None:
            print(f"[act    ] Invoke() on {args.aid!r}, then wait {args.delay}s")
            _kill()
            time.sleep(args.delay)
        else:
            # The regime this probe exists for is NARROW: ElementFromPoint must
            # still find the button, and the provider must die before the
            # property reads. Invoking first and sleeping always overshoots it
            # (measured: at every delay from 0s up, the window was already gone
            # and the hit test landed on an unrelated window). Firing the kill
            # from a timer WHILE _inspect() runs is what lands inside it.
            import threading
            print(f"[act    ] Invoke() on {args.aid!r} from a timer at "
                  f"+{args.race * 1000:.0f}ms, concurrently with _inspect()")
            t = threading.Timer(args.race, _kill)
            t.daemon = True
            t.start()

        got = rec._inspect(ins, cx, cy) or {}
        print(f"[got    ] ct={got.get('controlType')!r} "
              f"aid={got.get('automationId')!r} name={got.get('name')!r} "
              f"strategy={got.get('locatorStrategy')!r} rect={got.get('rect')!r}")
        trace = getattr(ins, "_last_trace", None) or {}
        print(f"[trace  ] picked_by={trace.get('picked_by')!r}")
        print(f"[trace  ] raw={trace.get('raw')}")
        print()

        # Which of the two dead-click regimes did we land in? Only one of them
        # is a bug, and this probe cannot choose — UIA Invoke() tears the
        # window down faster than a real press->release->window.close() does,
        # so it usually overshoots into the second regime.
        raw_info = trace.get("raw_info") or {}
        raw_had_identity = bool(raw_info.get("automationId")
                                or raw_info.get("name"))
        recovered = (got.get("automationId") == args.aid
                     or (got.get("name")
                         and got.get("locatorStrategy") != "coordinate"))
        if recovered:
            print(f"PASS  the dead control was recovered: "
                  f"aid={got.get('automationId')!r} name={got.get('name')!r}")
            return 0
        if not raw_had_identity:
            print("SKIP  inconclusive — the window was ALREADY GONE when "
                  "element_at() hit-tested, so its first read landed on an "
                  f"unrelated element (raw={trace.get('raw')}). Nothing "
                  "identifies the click from a point any more, and an explicit "
                  "FAIL step is the correct outcome here (CLAUDE.md §3). This "
                  "probe cannot force the narrower regime it is meant to test; "
                  "reproduce that one with a real recording instead.")
            return 2
        print(f"FAIL  element_at()'s first read DID catch it alive "
              f"(raw={trace.get('raw')}) but the recovery did not use it — "
              f"result came back with strategy={got.get('locatorStrategy')!r}. "
              "This is the bug.")
        return 1
    finally:
        if proc is not None:
            try:
                proc.terminate()
            except Exception:
                pass


if __name__ == "__main__":
    sys.exit(main())
