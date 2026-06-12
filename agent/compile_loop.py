"""
compile_loop.py - Autonomous Java compile-validation loop
==========================================================
1. Inject mock events via mock_events internals
2. Call /api/generate to get two .java files
3. Save to test-runner/src/test/java/com/qaforge/tests/
4. Run mvn clean test-compile
5. If compile FAIL: parse error, fix SYSTEM_PROMPT; reset consecutive
   If generate FAIL (rate limit / network): wait + retry same attempt (no consecutive reset)
6. Repeat until 3 consecutive BUILD SUCCESS or MAX_ATTEMPTS compile attempts

Requires:
  - Express server running (node server/server.js)
  - GROQ_API_KEY env var set
  - Java 11 and Maven in PATH (or JAVA_HOME + MVN_BIN env vars)

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

MAX_COMPILE_ATTEMPTS = 10   # max times we actually try to compile (not counting gen retries)
MAX_GEN_RETRIES = 8         # max retries per compile attempt when Groq is unavailable
REQUIRED_CONSECUTIVE = 3
GEN_RETRY_DELAY = 35        # seconds between Groq retries (rate limit window)

PASS_TAG = "[PASS]"
FAIL_TAG = "[FAIL]"
WARN_TAG = "[WARN]"


def req(method, path, body=None, timeout=120):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"} if data else {}
    r = urllib.request.Request(f"{BASE}{path}", data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read())
        except Exception:
            return e.code, {"error": str(e)}
    except Exception as ex:
        return 0, {"error": str(ex)}


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
    """Returns (files, error_message). files=None on failure."""
    status, body = req("POST", "/api/generate", {
        "apiKey": api_key,
        "appName": APP_NAME,
        "platform": PLATFORM,
    }, timeout=120)
    if status == 200 and body.get("ok"):
        return body["files"], None
    msg = body.get("message", f"HTTP {status}")
    return None, msg


def save_files(files):
    os.makedirs(TEST_DIR, exist_ok=True)
    for f in files:
        path = os.path.join(TEST_DIR, f["filename"])
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(f["content"])
        print(f"    Saved: {f['filename']} ({len(f['content'])} chars)")


def mvn_compile():
    env = os.environ.copy()
    env["JAVA_HOME"] = JAVA_HOME
    env["PATH"] = (os.path.join(JAVA_HOME, "bin") + os.pathsep +
                   os.path.dirname(MVN_BIN) + os.pathsep + env.get("PATH", ""))
    result = subprocess.run(
        [MVN_BIN, "clean", "test-compile"],
        cwd=RUNNER_DIR,
        capture_output=True,
        text=True,
        env=env,
        timeout=180,
    )
    return result.returncode == 0, result.stdout + result.stderr


def parse_compile_errors(output):
    """Return the most useful error lines from javac output."""
    lines = output.splitlines()
    errors = []
    for i, line in enumerate(lines):
        if "error:" in line.lower() and "BUILD" not in line:
            # include 2 lines of context
            errors.append(line)
            if i + 1 < len(lines):
                errors.append("  " + lines[i + 1])
    return errors[:20]


def main():
    api_key = os.environ.get("GROQ_API_KEY", "")
    if not api_key:
        print("ERROR: GROQ_API_KEY not set.")
        sys.exit(1)

    print("=" * 60)
    print("  compile_loop.py - Java compile validation loop")
    print(f"  Target: {MAX_COMPILE_ATTEMPTS} compile attempts, {REQUIRED_CONSECUTIVE} consecutive needed")
    print("=" * 60)

    consecutive = 0
    compile_attempt = 0

    while compile_attempt < MAX_COMPILE_ATTEMPTS and consecutive < REQUIRED_CONSECUTIVE:
        compile_attempt += 1
        print(f"\n--- Compile attempt {compile_attempt}/{MAX_COMPILE_ATTEMPTS} "
              f"(consecutive: {consecutive}/{REQUIRED_CONSECUTIVE}) ---")

        # Step 1: inject events
        if not inject_events():
            print(f"  {FAIL_TAG} Could not inject events — is the server running?")
            consecutive = 0
            time.sleep(5)
            continue

        # Step 2: generate with retry loop for rate limiting
        files = None
        for gen_try in range(1, MAX_GEN_RETRIES + 1):
            print(f"  [gen try {gen_try}/{MAX_GEN_RETRIES}] Calling /api/generate...")
            files, err_msg = generate_files(api_key)
            if files:
                break
            is_rate_limit = "rate limit" in (err_msg or "").lower() or "429" in (err_msg or "")
            is_auth = "401" in (err_msg or "") or "api key" in (err_msg or "").lower()
            print(f"  {WARN_TAG} Generation failed: {err_msg}")
            if is_auth:
                print(f"  {FAIL_TAG} Auth error — check GROQ_API_KEY")
                sys.exit(1)
            if gen_try < MAX_GEN_RETRIES:
                delay = GEN_RETRY_DELAY if is_rate_limit else 10
                print(f"  Waiting {delay}s before retry...")
                time.sleep(delay)

        if not files:
            print(f"  {FAIL_TAG} Could not get generated files after {MAX_GEN_RETRIES} tries — skipping compile")
            # Don't reset consecutive — this is a Groq availability issue, not code quality
            print(f"  {WARN_TAG} consecutive stays at {consecutive} (gen failure is not a code issue)")
            time.sleep(10)
            continue

        # Step 3: save
        print("  [3] Saving files...")
        save_files(files)

        # Step 4: compile
        print("  [4] Running mvn clean test-compile...")
        ok, output = mvn_compile()

        if ok:
            consecutive += 1
            print(f"  {PASS_TAG} BUILD SUCCESS (consecutive: {consecutive}/{REQUIRED_CONSECUTIVE})")
        else:
            consecutive = 0
            errors = parse_compile_errors(output)
            print(f"  {FAIL_TAG} BUILD FAILED")
            if errors:
                print("  Compiler errors:")
                for e in errors:
                    print(f"    {e}")
            else:
                # Show raw [ERROR] lines as fallback
                for line in output.splitlines():
                    if "[ERROR]" in line:
                        print(f"    {line}")
            print()
            print("  NOTE: Fix SYSTEM_PROMPT in server.js if this error is systematic.")
            print("  The generated files are saved for inspection:")
            for fname in os.listdir(TEST_DIR):
                if fname.endswith(".java"):
                    print(f"    {os.path.join(TEST_DIR, fname)}")

        # Brief pause between compile attempts to avoid hammering Groq
        if consecutive < REQUIRED_CONSECUTIVE and compile_attempt < MAX_COMPILE_ATTEMPTS:
            time.sleep(8)

    print(f"\n{'=' * 60}")
    if consecutive >= REQUIRED_CONSECUTIVE:
        print(f"  SUCCESS: {REQUIRED_CONSECUTIVE} consecutive BUILD SUCCESS in {compile_attempt} compile attempts")
    else:
        print(f"  INCOMPLETE: {consecutive} consecutive passes after {compile_attempt} compile attempts")
    print("=" * 60)

    return consecutive >= REQUIRED_CONSECUTIVE


if __name__ == "__main__":
    ok = main()
    sys.exit(0 if ok else 1)
