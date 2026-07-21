---
description: Root-cause a pasted replay log and apply the smallest possible fix — never touch anything the log doesn't implicate
argument-hint: "[paste the full node <App>TestById.js / TestByClass.js console output]"
---

You are given a real execution log from running a generated test
(`generated-wdio/<App>/<App>TestById.js` or `...TestByClass.js`). The log is
in `$ARGUMENTS` (or in the message right after this command if `$ARGUMENTS`
is empty — ask me to paste it if you truly have nothing to work with).

**Goal**: fix exactly the failure(s) visible in the log. Nothing else.

This is a surgical-fix workflow, not a refactor pass. The generator
(`server/server.js`) and capture agent (`agent/agent.py`) both have a lot of
hard-won, narrow fixes for specific past bugs (see `CLAUDE.md` §4/§5) — an
unrelated "improvement" here has historically broken one of those. Treat
every line you are not fixing as load-bearing.

## Steps

1. **Parse the log.** Pull out every signal: `[FAIL]` array contents,
   `[STEP]` labels leading up to a failure, `[osScopedInvoke]`/
   `[osExpandCollapse]`/`[osScroll]`/`[osDismissPopup]` failure lines,
   `[replay-warnings]`, stack traces, `esc-recovery-closed-app`. Note the
   exact STEP number(s) and step labels involved — you will cite these later.

2. **Root-cause each distinct failure before touching anything.**
   - Read the actual generated file (`generated-wdio/<App>/<App>TestById.js`
     or `ByClass.js`) to see exactly what code ran for the failing STEP —
     do not guess from the label alone.
   - Trace that generated code back to the template/function in
     `server/server.js` (or the capture logic in `agent/agent.py`) that
     produced it.
   - Check `CLAUDE.md` §4 (Current Status) and §5 (Known Traps) for whether
     this exact failure shape was already investigated — if so, verify
     whether the documented fix is actually present in the current source
     (grep for it) rather than assuming the changelog entry means the code
     still has it.
   - If two failures in the same log share one root cause, say so and plan
     one fix — but if they're independent, plan independent fixes and do not
     conflate them.

3. **State the fix plan before editing**, tied explicitly to log lines:
   for each failure, name the exact function/line(s) you intend to change
   and quote the log line(s) that justify the change. If you cannot tie a
   proposed change to a specific observed symptom, do not make that change —
   stop and ask instead of guessing.

4. **Apply the smallest possible diff.**
   - Touch only the function(s) identified in step 3.
   - Do not rename variables, reformat, reorder, or "clean up" surrounding
     code, even if it looks improvable — that is out of scope for this
     workflow and risks silently breaking a different app's replay path.
   - Do not edit `generated-wdio/*` directly (project hard rule — it's
     regenerated output; fix the template in `server.js`/`agent.py` instead).
   - If the fix is in `agent.py`, remind me at the end that it needs an
     admin-terminal restart to take effect (no hot reload).

5. **Verify no regression.**
   - `node --check server/server.js` if server.js changed;
     `python -m py_compile agent/agent.py` if agent.py changed.
   - `python agent/mock_events.py` — the gate count must not go down. If your
     fix is substantial enough to deserve its own regression case, add ONE
     targeted case (following the existing `MockNative`/`MockCollision`/etc.
     pattern) — but only for the exact behavior you just fixed, not a general
     hardening pass.

6. **Report**, per failure found in the log:
   - what the log showed (quote it),
   - the confirmed root cause,
   - the exact diff applied (file + line range),
   - the regression gate count before → after,
   - explicit confirmation of what you deliberately left untouched and why
     (e.g. "STEP 9-11 expandCollapse calls succeeded in this log — not
     touched").

Do not run `node <App>TestById.js` yourself — you can't see the live GUI. If
you want the fix re-verified live, tell me which STEP numbers to watch and
I'll run it and paste the next log back to you (round-trip with this same
command).
