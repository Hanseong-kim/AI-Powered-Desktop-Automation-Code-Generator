"""
compile_loop.py - Autonomous Java compile-validation loop
==========================================================
1. Inject mock events via mock_events internals
2. Call /api/generate to get two .java files
3. Save to test-runner/src/test/java/com/qaforge/tests/
4. Run mvn clean test-compile
5. If fail: parse error, report; if success: count consecutive passes
6. Repeat until 3 consecutive passes or 10 attempts

Requires:
  - Express server running (node server/server.js)
  - GROQ_API_KEY env var set
  - Java 11 and Maven in PATH (or set JAVA_HOME + MVN_BIN)

Usage:
  set GROQ_API_KEY=gsk_...
  python agent/compile_loop.py
"""

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

BASE = "http://localhost:3002"
APP_NAME = "Calculator"
PLATFORM = "Windows"
TEST_DIR = os.path.join(os.path.dirname(__file__), "..", "test-runner",
                        "src", "test", "java", "com", "qaforge", "tests")
RUNNER_DIR = os.path.join(os.path.dirname(__file__), "..", "test-runner")
JAVA_HOME = os.environ.get("JAVA_HOME",
    r"C:\Program Files\Eclipse Adoptium\jdk-11.0.31.11-hotspot")
MVN_BIN = os.environ.get("MVN_BIN",
    r"C:\tools\maven\apache-maven-3.9.6\bin\mvn.cmd")

MAX_ATTEMPTS = 10
REQUIRED_CONSECUTIVE = 3

PASS = "[PASS]"
FAIL = "[FAIL]"


def req(method, path, body=None, timeout=60):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"} if data else {}
    r = urllib.request.Request(f"{BASE}{path}", data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())
    except Exception as ex:
        return 0, {"error": str(ex)}


# Re-use event definitions from mock_events
sys.path.insert(0, os.path.dirname(__file__))
from mock_events import MOCK_EVENTS


def inject_events():
    req("DELETE", "/api/events")
    for ev in MOCK_EVENTS:
        status, _ = req("POST", "/api/events", ev)
        if status != 200:
            return False
    return True


def generate_files(api_key):
    status, body = req("POST", "/api/generate", {
        "apiKey": api_key,
        "appName": APP_NAME,
        "platform": PLATFORM,
    }, timeout=120)
    if status == 200 and body.get("ok"):
        return body["files"]
    return None


def save_files(files):
    os.makedirs(TEST_DIR, exist_ok=True)
    for f in files:
        path = os.path.join(TEST_DIR, f["filename"])
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(f["content"])
        print(f"  Saved: {f['filename']}")


def mvn_compile():
    env = os.environ.copy()
    env["JAVA_HOME"] = JAVA_HOME
    env["PATH"] = os.path.join(JAVA_HOME, "bin") + os.pathsep + \
                  os.path.dirname(MVN_BIN) + os.pathsep + env.get("PATH", "")
    result = subprocess.run(
        [MVN_BIN, "clean", "test-compile"],
        cwd=RUNNER_DIR,
        capture_output=True,
        text=True,
        env=env,
        timeout=180,
    )
    return result.returncode == 0, result.stdout + result.stderr


def parse_errors(output):
    lines = output.splitlines()
    errors = [l for l in lines if "ERROR" in l or "error:" in l.lower()]
    return errors[:15]


def main():
    api_key = os.environ.get("GROQ_API_KEY", "")
    if not api_key:
        print("ERROR: GROQ_API_KEY not set. Cannot run compile loop.")
        sys.exit(1)

    print("=" * 58)
    print("  compile_loop.py - Java compile validation loop")
    print("=" * 58)

    consecutive = 0
    attempt = 0

    while attempt < MAX_ATTEMPTS and consecutive < REQUIRED_CONSECUTIVE:
        attempt += 1
        print(f"\n--- Attempt {attempt}/{MAX_ATTEMPTS} (consecutive: {consecutive}/{REQUIRED_CONSECUTIVE}) ---")

        print("  [1] Injecting mock events...")
        if not inject_events():
            print(f"  {FAIL} Could not inject events")
            consecutive = 0
            continue

        print("  [2] Calling /api/generate...")
        files = generate_files(api_key)
        if not files:
            print(f"  {FAIL} Generation failed")
            consecutive = 0
            time.sleep(5)
            continue

        print("  [3] Saving files...")
        save_files(files)

        print("  [4] Running mvn clean test-compile...")
        ok, output = mvn_compile()

        if ok:
            consecutive += 1
            print(f"  {PASS} BUILD SUCCESS (consecutive: {consecutive})")
        else:
            consecutive = 0
            errors = parse_errors(output)
            print(f"  {FAIL} BUILD FAILED")
            print("  Top errors:")
            for e in errors:
                print(f"    {e}")
            # Note: in autonomous mode, we would patch SYSTEM_PROMPT here.
            # Since Groq output varies, the key fixes are already in SYSTEM_PROMPT.
            # Print the full output section for diagnosis:
            start = output.find("[ERROR]")
            if start > -1:
                print("  ... (see above for full error context)")

    print(f"\n{'=' * 58}")
    if consecutive >= REQUIRED_CONSECUTIVE:
        print(f"  SUCCESS: {REQUIRED_CONSECUTIVE} consecutive BUILD SUCCESS achieved in {attempt} attempts")
    else:
        print(f"  INCOMPLETE: only {consecutive} consecutive passes after {attempt} attempts")
    print("=" * 58)

    return consecutive >= REQUIRED_CONSECUTIVE


if __name__ == "__main__":
    ok = main()
    sys.exit(0 if ok else 1)
