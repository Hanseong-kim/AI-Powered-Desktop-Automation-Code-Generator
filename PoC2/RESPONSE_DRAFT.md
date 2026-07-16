# Re: PowerShell/Python helper files — why they exist, and why we're not inlining them

To: Hamza (cc Junaid)
From: hansung (Jacob) Kim
Re: Review huddle 2026-07-16, action item "research PowerShell file
necessity and provide solution or justification for their use"

---

## Short answer

Every generated test folder ships 9 small helper scripts (6 `.ps1` + 3
`.py`, ~850 lines total) alongside the two `.js` test files. They exist
because **WinAppDriver's REST API is missing capabilities the project
needs** — it has no scroll endpoint, a session can only ever see one
window, and its accessibility bridge can't reach some legacy native
controls. Each helper works around exactly one of those gaps, using
Windows UI Automation directly instead of going through WinAppDriver.

I also looked into your suggestion — embedding the script code directly
into the generated `.js` instead of writing sibling files. It's possible
for one specific case (already done, see below) but not for the rest:
the scripts that matter most (popup recovery, scroll, cross-window
clicks) all take runtime arguments (which window handle, which element,
which direction), and PowerShell's inline-execution mechanism
(`-EncodedCommand`) has no clean way to pass those without re-encoding
the whole script on every single call. Doing that would bloat every
generated test file with duplicated base64 blobs and make failures
harder to debug (no readable file to open, just an opaque string in the
stack trace). I think the current approach — small, single-purpose
sibling files — is the right tradeoff, but happy to revisit if you see
it differently.

## Why WinAppDriver alone isn't enough (the 3 concrete gaps)

1. **No scroll API.** WinAppDriver's REST surface has nothing for
   scrolling — the only way to fake it through REST would be injecting
   physical mouse-wheel signals at screen coordinates, which is exactly
   the "coordinates as a fallback" approach we already ruled out
   (breaks on window move/resize/different resolution). `osScroll.py`
   calls Windows' `ScrollPattern` UIA API directly instead — measured
   live on File Explorer, scroll position moved 0 → 0.374 with zero
   pixel-coordinate calls. See `poc/FINDINGS.md` PoC②.

2. **A WinAppDriver session is pinned to one window for its whole
   life.** We measured this directly: even with 10+ other windows open
   on the desktop, a session's `window_handles` call only ever returns
   the single window handle it was created against — there's no
   browser-style "switch to window 2" within one session. So when a
   popup or a second dialog opens, replaying a click inside it requires
   either creating a brand-new WinAppDriver session for that window
   (slow — up to 15-20s per new session) or reaching it directly through
   Windows UI Automation instead. `osScopedInvoke.py` does the latter —
   it finds and clicks elements in a specific window by its handle,
   with no REST session involved at all. See `poc/FINDINGS.md` PoC③,
   confirmed by measurement, not assumption.

3. **WinAppDriver can't see into some real native controls.** We hit
   this concretely on PuTTY: a "Window" tree needed to expand via
   Windows' `ExpandCollapsePattern`, but WinAppDriver's accessibility
   bridge (built on .NET's managed UIA) simply doesn't expose that
   legacy Win32 tree control's internals — `ExpandCollapsePattern not
   supported` even though the control clearly supports it. Switching to
   calling the OS's UI Automation COM interface directly
   (`osExpandCollapse.py`) fixed it immediately, same control, same
   click target. Full evidence trail is in this project's `CLAUDE.md`,
   dated 2026-07-14.

## What we tried for "no separate files"

There's already one helper (`osForegroundHwnd()`) that's fully inlined —
zero sibling file, the script text is base64-encoded directly into the
generated `.js`. It works because that script is tiny (6 lines) and
takes **no arguments** — read-only "what window is focused right now."

The other 8 helpers all need arguments at call time (which window
handle to act on, which element to search for, which direction to
scroll). PowerShell's `-EncodedCommand` inlining technique doesn't have
a way to pass parameters into an encoded blob — you'd have to
re-generate and re-encode the whole script text for every single call
site in every generated test, which means:
- the same ~130-190 lines of base64 duplicated dozens of times across
  one test file instead of living once in a shared sibling file, and
- when a call fails, the error points into an opaque base64 string
  instead of a named `.ps1`/`.py` file you can actually open and read.

So: one helper is inlined where it makes sense (no args, tiny), and the
rest stay as sibling files because the alternative is strictly worse on
every axis we could measure — file size, debuggability, and
duplication. Full technical breakdown with line counts and code
citations is in `EVIDENCE.md` in this same folder.

## Bottom line

The helper files aren't incidental scaffolding — each one exists to
cover a documented, measured gap in what WinAppDriver's REST API can do
on its own. Happy to walk through any of the three PoCs live if useful.
