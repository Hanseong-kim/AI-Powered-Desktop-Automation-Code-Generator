"""
verify_replay.py -- catches a REST "success" that never touched the screen.

Runs a generated `<App>TestById.js` the normal way (`node <App>TestById.js`),
and while it runs, independently re-reads the LIVE UIA state of each `type`
step's target field via COM -- the same stack `agent.py`/`osScopedInvoke.py`
already use -- and compares it against what the recording expected. Prints a
log in the same spirit as agent.py's capture-time [inspect] lines, so it can
be read side by side with the original capture log.

WHY THIS EXISTS: measured live 2026-08-06 (FileZilla Site Manager) --
`[type] scoped sendKeys ok` / `_typeScoped returned true` for three fields
(호스트/사용자명/비밀번호), and every downstream step that depended on those
values succeeding (Quick Connect opening a "remember password?" dialog) then
failed with "window not found" -- because the fields were never actually
filled. WinAppDriver's `element/value` reporting success is not proof the app
saw the keystrokes (CLAUDE.md §3, "No false PASS") -- this exists to make
that visible instead of discovered by eye.

v1 scope: `type` steps only (ValuePattern.CurrentValue after each one). Click
verification generalized beyond checkboxes is a separate, larger effort --
see the plan this was built from.

DESIGN RULE (matches agent/sweep/common.py): do not reimplement UIA
primitives that already exist. get_uia/describe/all_windows/pids_for_image
come from poc/probe_app_automatability.py via agent/sweep/common.py.

Usage:
    python agent/verify_replay.py --app FileZilla
    python agent/verify_replay.py --app FileZilla --strategy byclass
"""

import argparse
import ctypes
import io
import json
import os
import re
import subprocess
import sys
import time

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "sweep"))
from common import get_uia, all_windows  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.abspath(__file__)).rsplit(os.sep, 1)[0]
WDIO_DIR = os.path.join(REPO_ROOT, "generated-wdio")

UIA_ValuePatternId = 10002
UIA_IsPasswordPropertyId = 30019
TreeScope_Subtree = 7

STEP_RE = re.compile(r"\[STEP\]\s*(\d+):")


def log(*args):
    print("[verify]", *args, flush=True)


def load_manifest(app):
    path = os.path.join(WDIO_DIR, app, f"{app}VerifyManifest.json")
    if not os.path.exists(path):
        raise SystemExit(
            f"verify_replay: {path} not found -- run POST /api/generate for "
            f"'{app}' first (server.js writes this sidecar every generate)."
        )
    with open(path, "r", encoding="utf-8") as f:
        entries = json.load(f)
    return {e["step"]: e for e in entries}


def resolve_cond(uia, selector):
    """Mirrors OS_SCOPEDINVOKE_PY's resolve_cond() in server.js -- AND every
    non-empty field. Kept structurally identical on purpose: if this ever
    finds a DIFFERENT element than replay's own osScopedInvoke.py did, that
    divergence itself would be a bug worth knowing about."""
    conds = []
    if selector.get("automationId"):
        conds.append(uia.CreatePropertyCondition(30011, selector["automationId"]))
    if selector.get("name"):
        conds.append(uia.CreatePropertyCondition(30005, selector["name"]))
    if selector.get("className"):
        conds.append(uia.CreatePropertyCondition(30012, selector["className"]))
    if not conds:
        return None
    cond = conds[0]
    for c in conds[1:]:
        cond = uia.CreateAndCondition(cond, c)
    return cond


def find_windows_by_title(title_fragment):
    if not title_fragment:
        return []
    frag = title_fragment.lower()
    return [w for w in all_windows() if frag in w["title"].lower()]


def find_live_element(uia, selector, window_title, attempts=6, delay=0.3):
    """Search order mirrors osScopedInvoke.py: the window it was recorded in
    first, then any other visible top-level window sharing that window's PID
    (a cross-window dialog may have moved/renamed by the time we look)."""
    cond = resolve_cond(uia, selector)
    if cond is None:
        return None, "selector has no usable fields"

    for attempt in range(attempts):
        if attempt > 0:
            time.sleep(delay)

        candidates = find_windows_by_title(window_title)
        for w in candidates:
            try:
                root = uia.ElementFromHandle(w["hwnd"])
                if not root:
                    continue
                el = root.FindFirst(TreeScope_Subtree, cond)
                if el:
                    return el, None
            except Exception:
                continue

        # Same-PID fallback (mirrors osScopedInvoke.py's step (b)) -- the
        # window's title may have drifted since the recorded windowTitle.
        if candidates:
            pid = candidates[0]["pid"]
            for w in all_windows():
                if w["pid"] != pid:
                    continue
                try:
                    root = uia.ElementFromHandle(w["hwnd"])
                    if not root:
                        continue
                    el = root.FindFirst(TreeScope_Subtree, cond)
                    if el:
                        return el, None
                except Exception:
                    continue

    return None, f"not found under window {window_title!r} or any same-PID window after {attempts} attempts"


def read_value(uia, mod, el):
    try:
        vp = el.GetCurrentPattern(UIA_ValuePatternId).QueryInterface(mod.IUIAutomationValuePattern)
        return vp.CurrentValue, None
    except Exception as e:
        return None, f"ValuePattern unavailable: {e}"


def classify_value_result(is_password, expected, live_value):
    """Keep protected fields out of the false-mismatch bucket.

    UIA deliberately hides password values. An empty value on a password edit
    therefore says nothing about whether input landed and must be reported as
    opaque rather than as a failed replay action.
    """
    if is_password:
        return "opaque"
    return "match" if live_value == expected else "mismatch"


def is_password_field(el):
    try:
        return bool(el.GetCurrentPropertyValue(UIA_IsPasswordPropertyId))
    except Exception:
        return False


def verify_step(uia, mod, entry):
    step = entry["step"]
    expected = entry["expectedValue"]
    selector = entry["selector"]
    window_title = entry["windowTitle"]

    el, err = find_live_element(uia, selector, window_title)
    if el is None:
        log(f"STEP {step} type {expected!r} -> element not found ({err}) -- MISMATCH")
        return "element-not-found"

    if is_password_field(el):
        log(f"STEP {step} type <password> -> protected value is not read -- OPAQUE")
        return "opaque"

    live_value, err = read_value(uia, mod, el)
    if err:
        log(f"STEP {step} type {expected!r} -> {err} -- MISMATCH")
        return "no-value-pattern"

    verdict = classify_value_result(False, expected, live_value)
    if verdict == "match":
        log(f"STEP {step} type {expected!r} -> live value={live_value!r} MATCH")
        return "match"
    log(f"STEP {step} type {expected!r} -> live value={live_value!r} MISMATCH")
    return "mismatch"


def run(app, strategy="byid"):
    manifest = load_manifest(app)
    if not manifest:
        log(f"{app}VerifyManifest.json has no type steps -- nothing to verify")
        return {"verified": 0, "match": 0, "mismatch": 0}

    script = f"{app}Test{'ById' if strategy == 'byid' else 'ByClass'}.js"
    folder = os.path.join(WDIO_DIR, app)
    script_path = os.path.join(folder, script)
    if not os.path.exists(script_path):
        raise SystemExit(f"verify_replay: {script_path} not found")

    log(f"launching: node {script}  (cwd={folder})")
    log(f"{len(manifest)} type step(s) to verify: {sorted(manifest)}")

    uia, mod = get_uia()

    proc = subprocess.Popen(
        ["node", script], cwd=folder,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", bufsize=1,
    )

    results = {}
    pending_step = None  # the step that just started, verified once the NEXT boundary arrives

    def maybe_verify(step_num):
        if step_num in manifest and step_num not in results:
            results[step_num] = verify_step(uia, mod, manifest[step_num])

    try:
        for line in proc.stdout:
            print(line, end="")  # pass the replay's own log straight through
            m = STEP_RE.search(line)
            if m:
                new_step = int(m.group(1))
                if pending_step is not None:
                    maybe_verify(pending_step)
                pending_step = new_step
    finally:
        proc.wait(timeout=30)
        if pending_step is not None:
            maybe_verify(pending_step)

    match = sum(1 for v in results.values() if v in ("match", "opaque"))
    opaque = sum(1 for v in results.values() if v == "opaque")
    mismatch = len(results) - match
    log("=" * 60)
    log(f"{match}/{len(manifest)} type step(s) verified landing correctly")
    if opaque:
        log(f"{opaque} protected password step(s) accepted as opaque (not value-readable)")
    if mismatch:
        log(f"{mismatch} step(s) MISMATCH -- see [verify] lines above for which ones")
    log("=" * 60)
    return {"verified": len(results), "match": match, "mismatch": mismatch, "results": results}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--app", required=True)
    ap.add_argument("--strategy", choices=["byid", "byclass"], default="byid")
    args = ap.parse_args()
    result = run(args.app, args.strategy)
    return 1 if result.get("mismatch") else 0


if __name__ == "__main__":
    sys.exit(main())
