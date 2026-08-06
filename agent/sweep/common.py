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
            "platform": g.get("platform", "Windows"),
            "titleHint": o.get("titleHint", ""),
            "denyNames": o.get("denyNames", []),
            "allowNames": o.get("allowNames", []),
            "enabled": o.get("enabled", True),
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
    rx = re.compile(pat)
    if not allow:
        return rx
    allow_rx = re.compile("|".join(allow))

    class _Rx:
        def search(self, s):
            if allow_rx.search(s):
                return None
            return rx.search(s)
    return _Rx()


# ───────────────────────────────────────────────────────────────── reports
def write_report(app, tier, payload):
    os.makedirs(REPORTS_DIR, exist_ok=True)
    path = os.path.join(REPORTS_DIR, "%s-%s.json" % (app, tier))
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    return path


def read_controls(app):
    path = os.path.join(CONTROLS_DIR, "%s.json" % app)
    if not os.path.exists(path):
        raise SystemExit(
            "sweep: no enumeration cache for %s.\n"
            "  run first:  python agent/sweep/run.py enumerate --app %s"
            % (app, app))
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def write_controls(app, payload):
    os.makedirs(CONTROLS_DIR, exist_ok=True)
    path = os.path.join(CONTROLS_DIR, "%s.json" % app)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    return path


def server_up():
    status, body = request("GET", "/api/status")
    return status == 200, body
