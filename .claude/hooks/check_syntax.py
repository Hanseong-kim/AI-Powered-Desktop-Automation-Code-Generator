#!/usr/bin/env python
"""
PostToolUse hook: after Claude edits the two source-of-truth files, run the
same syntax gate a human would run by hand, so a broken edit is caught
immediately instead of at the next `node server.js` / `python agent.py`.

- server/server.js        -> `node --check`
- agent/agent.py          -> `python -m py_compile`
- agent/mock_events.py    -> `python -m py_compile`  (regression harness)

Generated artifacts under generated-wdio/ are intentionally ignored — they are
overwritten every Generate and are not edited by hand (project hard rule).

Reads the hook payload as JSON on stdin. Exits 2 with the error on stderr when
a check fails (fed back to Claude); exits 0 silently otherwise.
"""
import json
import subprocess
import sys


def main():
    try:
        # Read bytes and decode with utf-8-sig so a stray BOM (some shells add
        # one when piping) doesn't break json parsing.
        raw = sys.stdin.buffer.read().decode("utf-8-sig")
        data = json.loads(raw)
    except Exception:
        return 0  # no/invalid payload — nothing to check

    fp = (data.get("tool_input") or {}).get("file_path") or ""
    if not fp:
        return 0
    norm = fp.replace("\\", "/")

    if norm.endswith("/server/server.js") or norm.endswith("server/server.js"):
        r = subprocess.run(["node", "--check", fp], capture_output=True, text=True)
        if r.returncode != 0:
            sys.stderr.write(
                "[hook:check_syntax] node --check FAILED for server.js - fix before continuing:\n"
                + (r.stderr or r.stdout).strip() + "\n"
            )
            return 2
    elif norm.endswith("/agent.py") or norm.endswith("/mock_events.py"):
        r = subprocess.run([sys.executable, "-m", "py_compile", fp],
                           capture_output=True, text=True)
        if r.returncode != 0:
            sys.stderr.write(
                "[hook:check_syntax] py_compile FAILED - fix before continuing:\n"
                + (r.stderr or r.stdout).strip() + "\n"
            )
            return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
