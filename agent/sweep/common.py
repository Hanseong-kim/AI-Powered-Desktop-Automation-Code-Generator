"""
Shared plumbing for the control sweep harness.

DESIGN RULE FOR THIS PACKAGE: do not reimplement anything that already exists.
Every UIA primitive here is imported from the two probes in `poc/`, and every
server/codegen primitive from `agent/mock_events.py`. If you find yourself
writing a second `describe()` or a second `make_event()`, stop -- the whole
point of this harness is that the pieces were already built and verified, and
only the loop wiring them together was missing.

    poc/probe_app_automatability.py   UIA tree walk, settle wait, describe
    poc/probe_click_replay.py         SendInput click + before/after verdicts
    agent/mock_events.py              HTTP helpers, event synthesis, JS audit

NOTE ON `poc/`: that folder is gitignored (see .gitignore "실행에 필요 없는
분석/도구 자료" block, 2026-07-27) but this harness genuinely needs it at
runtime. On a fresh clone the import below fails with an actionable message
rather than a traceback. If the sweep becomes part of the committed workflow,
un-ignore `poc/probe_app_automatability.py` and `poc/probe_click_replay.py`
instead of copying them here.
"""

import json
import os
import re
import sys
import time

SWEEP_DIR = os.path.dirname(os.path.abspath(__file__))
AGENT_DIR = os.path.dirname(SWEEP_DIR)
REPO_ROOT = os.path.dirname(AGENT_DIR)
POC_DIR = os.path.join(REPO_ROOT, "poc")
GOLDEN_MANIFEST = os.path.join(AGENT_DIR, "golden", "manifest.json")

CONTROLS_DIR = os.path.join(SWEEP_DIR, "controls")
REPORTS_DIR = os.path.join(SWEEP_DIR, "reports")
LEARNED_DENY = os.path.join(SWEEP_DIR, "controls", "_learned_deny.json")

for _p in (POC_DIR, AGENT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    from probe_app_automatability import (            # noqa: F401
        get_uia, describe, patterns_of, activate, all_windows, pids_for_image,
        find_all, find_all_settled,
        CONTROL_TYPE_NAMES, CONTAINERS, INTERACTIVE, PATTERN_IDS,
    )
    from probe_click_replay import (                  # noqa: F401
        send_click, snapshot, top_level_windows_snapshot, is_owner_drawn_suspect,
        capture_one,
    )
except ImportError as e:
    raise SystemExit(
        "sweep: cannot import the poc/ probes -- they are this harness's UIA "
        "engine and are not optional.\n"
        "  expected: %s\\{probe_app_automatability.py, probe_click_replay.py}\n"
        "  cause:    %s\n"
        "Note that poc/ is gitignored, so a fresh clone will not have them."
        % (POC_DIR, e)
    )

from mock_events import (                             # noqa: E402,F401
    request, make_event, check_helpers_defined, _strip_embedded_helpers,
)


# ───────────────────────────────────────────────────────────────── safety
# Names whose click is assumed destructive until proven otherwise. This list
# LEAKS -- an icon-only toolbar button carries no name at all (measured
# 2026-08-05 on 7-Zip: ControlType=Button with name/automationId/className all
# empty), so nothing here can classify it. That is what the learned denylist
# below is for: anything that kills the app window once is never clicked again.
DEFAULT_DENY = (
    r"삭제|제거|지우기|비우기|포맷|초기화|복원|되돌리기|종료|끝내기|나가기|"
    r"닫기|로그아웃|해제|덮어쓰기|"
    r"\bDelete\b|\bRemove\b|\bErase\b|\bFormat\b|\bReset\b|\bRestore\b|"
    r"\bExit\b|\bQuit\b|\bClose\b|\bUninstall\b|\bShutdown\b|\bLog ?out\b|"
    r"\bOverwrite\b|\bWipe\b"
)

# Matched case-INSENSITIVELY (2026-09-08). It was case-sensitive, and Medflow --
# whose every button label is upper case -- therefore had `EXIT`, `CLOSE` and
# `LOGOUT` classified SAFE: `\bExit\b` does not match `EXIT`. Tier 2 would have
# clicked the button that quits the app and only learned better afterwards, via
# the learned denylist.
#
# Blast radius measured over the six existing enumeration caches before making
# the change (324 clickable controls): exactly ONE name is newly denied,
# PuTTY's "Only on clean exit" radio, which is a false positive and is bought
# back by name in that app's allowNames below. So the change is provably
# no-op for every app already swept, and real protection for this one.
DENY_FLAGS = re.IGNORECASE

# ControlTypes that are structure, not an action target.
SKIP_CONTROL_TYPES = {"Window", "TitleBar", "Pane", "Separator", "ToolTip",
                      "ProgressBar", "ScrollBar", "Header", "Menu", "MenuBar"}

ACTIONABLE_PATTERNS = ("Invoke", "Toggle", "SelectionItem", "ExpandCollapse",
                       "Value", "Legacy")


def load_learned_deny():
    """{app: [control key, ...]} -- controls that killed the app once."""
    if not os.path.exists(LEARNED_DENY):
        return {}
    try:
        with open(LEARNED_DENY, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def add_learned_deny(app, key, reason):
    data = load_learned_deny()
    entry = data.setdefault(app, {})
    entry[key] = reason
    os.makedirs(os.path.dirname(LEARNED_DENY), exist_ok=True)
    with open(LEARNED_DENY, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)


def control_key(ctrl):
    """Stable identity for a control across launches -- deliberately excludes
    coordinates and the window rect, both of which move every launch."""
    return "|".join((ctrl.get("controlType", ""), ctrl.get("automationId", ""),
                     ctrl.get("name", ""), ctrl.get("className", "")))


def classify_safety(ctrl, deny_re, learned):
    """Returns (safety, reason). 'unsafe' controls are never clicked."""
    if control_key(ctrl) in learned:
        return "unsafe", "learned: %s" % learned[control_key(ctrl)]
    if ctrl["controlType"] in SKIP_CONTROL_TYPES:
        return "skip", "structural control type"
    name = ctrl.get("name") or ""
    if name and deny_re.search(name):
        return "unsafe", "name matches destructive pattern"
    return "safe", ""


# ───────────────────────────────────────────────────────────────── manifest
def load_manifest():
    """Golden manifest is the source of truth for app/exePath (do not add a
    third copy -- ControlPanel.jsx PRESETS and golden/manifest.json already
    duplicate this). sweep.json only layers on sweep-specific fields."""
    with open(GOLDEN_MANIFEST, encoding="utf-8") as fh:
        golden = json.load(fh)
    overrides = {}
    ov_path = os.path.join(SWEEP_DIR, "manifest.json")
    if os.path.exists(ov_path):
        with open(ov_path, encoding="utf-8") as fh:
            overrides = {o["app"]: o for o in json.load(fh)}

    out = []
    for g in golden:
        app = g["app"]
        o = overrides.get(app, {})
        out.append({
            "app": app,
            # NEVER the real preset name. Same reason golden uses MockGolden<App>
            # (mock_events.py:3043) -- reusing "SevenZip" here would overwrite
            # generated-wdio/SevenZip/ with an exePath-less synthetic build and
            # destroy a real verified capture.
            "appName": "Sweep" + app,
            "exePath": o.get("exePath", g["exePath"]),
            # Medflow runs as `mshta.exe <path>.hta`, so the exe alone launches
            # nothing usable and the generated launchApp() would have no
            # document to open. Carried from the golden manifest for the same
            # reason exePath is -- one source of truth, not a third copy.
            "exeArgs": o.get("exeArgs", g.get("exeArgs") or []),
            "platform": g.get("platform", "Windows"),
            "titleHint": o.get("titleHint", ""),
            "denyNames": o.get("denyNames", []),
            "allowNames": o.get("allowNames", []),
            "enabled": o.get("enabled", True),
            # {screenName: {"titleHint": ..., "prefix": [...]}} -- apps that
            # gate most of their controls behind a login have no way to reach
            # those screens after Tier 2's kill_app()+relaunch otherwise. See
            # run_prefix() below.
            "screens": o.get("screens", {}),
        })
    return out


def get_app(app_name):
    for e in load_manifest():
        if e["app"].lower() == app_name.lower():
            return e
    raise SystemExit("sweep: unknown app %r. known: %s"
                     % (app_name, ", ".join(e["app"] for e in load_manifest())))


def deny_regex(entry):
    pat = DEFAULT_DENY
    if entry.get("denyNames"):
        pat += "|" + "|".join(entry["denyNames"])
    allow = entry.get("allowNames") or []
    rx = re.compile(pat, DENY_FLAGS)
    if not allow:
        return rx
    allow_rx = re.compile("|".join(allow), DENY_FLAGS)

    class _Rx:
        def search(self, s):
            if allow_rx.search(s):
                return None
            return rx.search(s)
    return _Rx()


# ───────────────────────────────────────────────────────────── login prefix
# Medflow's Main (56 controls) and Settings (8 controls) screens are only
# reachable after logging in, and tier2_live.py kills and relaunches the app
# for every single control it tests (deliberately -- so no click inherits the
# previous one's state, see that module's docstring). Without this, every
# relaunch lands back on the Login screen and there is no way to reach the
# other 64 controls. This is the "설계 변경" identified in
# project_medflow_coverage_plan: a fixed sequence of navigation actions,
# defined per screen in sweep/manifest.json's "screens" field, replayed
# before the harness goes near the control actually under test.
#
# Deliberately NOT physical SendInput + agent.py capture. Only the one click
# that is the actual subject of a Tier 2 run needs to go through the real
# capture path (CLAUDE.md's whole reason for Tier 2 existing); these are
# setup steps to reach a screen, not something replay ever has to reproduce
# from a recording, so a direct COM UIA call (same primitives server.js's
# osScopedInvoke.py already uses at replay time: GetCurrentPattern(...)
# .QueryInterface(...).Invoke()/.SetValue()) is simpler and cannot itself
# introduce a false-PASS, since it plays no part in the thing being verified.
#
# 2026-09-09: implemented against the design agreed in
# project_medflow_coverage_plan, and now live-verified end to end against the
# real app -- agent/sweep/reports/Medflow-live.json, Button 'Administration'
# (nav_admin) on the Main screen: OK, replay exit code 0, "[PASS] all steps
# completed" from a cold launch through login into Main. Getting there
# surfaced THREE further, genuinely pre-existing bugs that had never been
# reachable before (every earlier Tier 2 attempt, any app, failed at an
# earlier stage than a real click):
#   1. agent.py's mouse hook unconditionally dropped every SendInput click as
#      "injected" -- fixed via TIER2_CLICK_EXTRA_INFO / _CaptureMouseListener
#      in agent.py, matching sentinel in poc/probe_click_replay.py's
#      send_click().
#   2. mshta.exe's launch has a real race that can produce two
#      near-simultaneous same-titled windows in different processes, so a
#      click landing in the window enum.launch_and_wait() rediscovers by
#      title can genuinely be a DIFFERENT window than the one agent.py
#      adopted as its recording target, no matter how long anything waits
#      (measured via agent.log: click's owning hwnd absent from agent's
#      tracked target_hwnds set) -- fixed via wait_for_agent_window() and
#      this function's require_agent_hwnd param, which require agreement
#      with agent.py's own /api/status-reported target_hwnds instead of
#      rediscovering blind.
#   3. A recording of just the tested control has no login in it, so cold
#      replay can never get past Login to reach it (measured: replayed
#      click-not-found for the real control) -- fixed via record_events=True
#      below, which splices synthetic login events in front of the real
#      captured one before /api/generate.
def get_agent_target_hwnds():
    """The window(s) agent.py is currently tracking, straight from
    /api/status. See the note above run_prefix() for why sweep must not
    independently rediscover "the" window by title alone when a real click
    is about to be captured through agent.py."""
    _, body = request("GET", "/api/status")
    return set(body.get("targetHwnds") or [])


def wait_for_agent_window(timeout=15.0):
    """Poll /api/status until agent.py reports at least one tracked window,
    then return the largest-area one in the same shape
    enum.launch_and_wait() returns. Use this (not an independent title
    search) whenever the window returned will have a real click captured
    through agent.py -- see the note above run_prefix()."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        tracked = get_agent_target_hwnds()
        if tracked:
            cands = [w for w in all_windows() if w["hwnd"] in tracked and w["area"] > 10000]
            if cands:
                return max(cands, key=lambda w: w["area"])
        time.sleep(0.3)
    raise RuntimeError(
        "sweep: agent.py reported no tracked window within %ds of /api/start "
        "-- check agent.log for [target] discovery failures" % timeout)


def _screen_id_of(title):
    return re.sub(r'[^a-z0-9]+', '_', (title or '').lower()).strip('_')


def _prefix_step_events(action, d, rect, cur_win, value, start_index):
    """Build the make_event()-shaped dict(s) for one prefix step, formatted
    to match a REAL agent.py capture byte-for-byte (cross-checked against
    agent/golden/recordings/MedflowDropdown.json's own login sequence,
    2026-09-09) -- rootHwndHex/isPopup/popupTitle/winLeft.../screenId on the
    click, none of that on the type that follows it, exactly as real
    recordings show. Returns a list because a click may need to carry
    different metadata from the type that follows the same resolved
    element."""
    left, top, right, bottom = cur_win["rect"]
    common = dict(
        name=d.get("name") or "", automation_id=d.get("automationId") or "",
        class_name=d.get("className") or "", control_type=d.get("controlType") or "Button",
        window_title=cur_win["title"], x=rect[0], y=rect[1], index=start_index,
        winLeft=left, winTop=top, winWidth=right - left, winHeight=bottom - top,
    )
    if action == "click":
        return [make_event(
            "click", isPopup=True, popupTitle=cur_win["title"],
            rootHwndHex=format(cur_win["hwnd"], "X"),
            screenId=_screen_id_of(cur_win["title"]), **common)]
    else:  # type
        return [make_event("type", value=value, **common)]


def run_prefix(uia, win, steps, timeout=20.0, require_agent_hwnd=False,
               record_events=False, start_index=1):
    """Replay a fixed navigation sequence to reach a screen that only exists
    after login. Returns (window, synth_events) -- window is wherever the
    sequence ends up; synth_events is [] unless record_events=True. Raises
    RuntimeError on any step that cannot be completed -- callers should
    treat that as a hard stop for this control, not something to retry with
    a guessed fallback (CLAUDE.md §3, no guessed clicks).

    require_agent_hwnd=True additionally requires each waitWindow step's
    match to be a window agent.py itself is tracking (see the note above),
    not just a title match -- pass this when a real click will be captured
    through agent.py afterwards (Tier 2). Tier 0/1 have no agent.py
    involvement at all, so they should leave this False.

    record_events=True additionally builds a make_event()-shaped dict for
    each type/click step, using the SAME live element this function already
    resolved to perform the action -- not a second, separately-authored copy
    of the same facts. WHY THIS EXISTS: a Tier 2 recording of just the one
    control under test has no login in it at all, so replaying it from a
    fresh launch can never get past the Login screen (confirmed 2026-09-09:
    a real Tier 2 run on Medflow's Main screen replayed
    'click-not-found:~nav_admin' for exactly this reason). Prepending these
    synthetic events to the real captured event before /api/generate gives
    replay the login it needs. start_index numbers them so the caller can
    renumber the real captured event(s) to continue right after."""
    import comtypes.client  # local import, mirrors agent.py's convention --
    # comtypes caches the generated module after the first call.
    mod = comtypes.client.GetModule("UIAutomationCore.dll")

    cur = win
    synth_events = []
    next_index = start_index
    for step in steps:
        action = step.get("action")
        if action in ("type", "click"):
            root = uia.ElementFromHandle(cur["hwnd"])
            if not root:  # comtypes: NULL COM pointer on miss, not None
                raise RuntimeError(
                    "prefix: window %r (hwnd=%s) had no UIA element for step %r"
                    % (cur.get("title"), cur.get("hwnd"), step))
            target = None
            target_d = None
            for el in find_all_settled(uia, root, timeout=6.0, quiet_for=0.8):
                d = describe(el)
                if step.get("automationId") and d["automationId"] == step["automationId"]:
                    target, target_d = el, d
                    break
                if not step.get("automationId") and step.get("name") \
                        and d["name"] == step["name"]:
                    target, target_d = el, d
                    break
            if target is None:
                raise RuntimeError(
                    "prefix: control not found for step %r in window %r"
                    % (step, cur.get("title")))
            try:
                r = target.CurrentBoundingRectangle
                rect = (r.left, r.top, r.right, r.bottom)
            except Exception:
                rect = (0, 0, 0, 0)
            if action == "type":
                pat = target.GetCurrentPattern(PATTERN_IDS["Value"])
                if not pat:
                    raise RuntimeError("prefix: step %r has no ValuePattern" % step)
                pat.QueryInterface(mod.IUIAutomationValuePattern).SetValue(step["value"])
            else:  # click
                pat = target.GetCurrentPattern(PATTERN_IDS["Invoke"])
                if pat:
                    pat.QueryInterface(mod.IUIAutomationInvokePattern).Invoke()
                else:
                    legacy = target.GetCurrentPattern(PATTERN_IDS["Legacy"])
                    if not legacy:
                        raise RuntimeError(
                            "prefix: step %r has neither Invoke nor Legacy pattern" % step)
                    legacy.QueryInterface(
                        mod.IUIAutomationLegacyIAccessiblePattern).DoDefaultAction()
            if record_events:
                evs = _prefix_step_events(action, target_d, rect, cur, step.get("value"), next_index)
                synth_events.extend(evs)
                next_index += len(evs)
        elif action == "waitWindow":
            frag = step["titleContains"].lower()
            deadline = time.time() + timeout
            found = None
            while time.time() < deadline:
                tracked = get_agent_target_hwnds() if require_agent_hwnd else None
                found = next((w for w in all_windows()
                             if frag in w["title"].lower() and w["area"] > 10000
                             and (tracked is None or w["hwnd"] in tracked)), None)
                if found:
                    break
                time.sleep(0.3)
            if not found:
                raise RuntimeError(
                    "prefix: no window matching %r appeared within %ds%s"
                    % (step["titleContains"], timeout,
                       " that agent.py is tracking -- a title-matching window "
                       "may exist but belongs to a different (untracked) "
                       "process; check agent.log's [target] hwnds"
                       if require_agent_hwnd else ""))
            activate(found["hwnd"])
            time.sleep(0.5)
            cur = found
        else:
            raise RuntimeError("prefix: unknown step action %r" % step)
    return cur, synth_events


# ───────────────────────────────────────────────────────────────── reports
def write_report(app, tier, payload):
    os.makedirs(REPORTS_DIR, exist_ok=True)
    path = os.path.join(REPORTS_DIR, "%s-%s.json" % (app, tier))
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    return path


def _controls_filename(app, screen=None):
    # One cache file per SCREEN, not per app -- enumerate_window() only ever
    # walks whichever window titleHint currently matches, so a multi-screen
    # app (Medflow: Login/Main/Settings/Detail all titled "Medflow ...")
    # would otherwise have each screen's enumeration silently overwrite the
    # last one. Apps with a single screen keep the old bare "<App>.json" name
    # (screen=None/""), so every existing cache file and every call site that
    # doesn't know about screens stays valid unchanged.
    return "%s__%s.json" % (app, screen) if screen else "%s.json" % app


def read_controls(app, screen=None):
    path = os.path.join(CONTROLS_DIR, _controls_filename(app, screen))
    if not os.path.exists(path):
        raise SystemExit(
            "sweep: no enumeration cache for %s.\n"
            "  run first:  python agent/sweep/run.py enumerate --app %s%s"
            % (app, app, (" --screen %s" % screen) if screen else ""))
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def write_controls(app, payload, screen=None):
    os.makedirs(CONTROLS_DIR, exist_ok=True)
    path = os.path.join(CONTROLS_DIR, _controls_filename(app, screen))
    # Some UIA elements report a garbled Name containing lone UTF-16
    # surrogates (raw-memory read artifact, same class as the "records
    # fine, replays as a no-op" signature probe_app_automatability.py
    # flags) -- utf-8 can't encode those. Replace them; the name is
    # already unusable garbage, not a real selector value.
    with open(path, "w", encoding="utf-8", errors="replace") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    return path


def server_up():
    status, body = request("GET", "/api/status")
    return status == 200, body
