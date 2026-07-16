# Re: review huddle feedback (2026-07-16) — all 3 items

To: Hamza (cc Junaid)
From: hansung (Jacob) Kim
Re: Review huddle 2026-07-16 — (1) PowerShell/Python helper files,
(2) multi-window session replay, (3) screen-2 element capture

---

## Summary

| # | Your feedback | Status |
|---|---|---|
| 1 | Why do PowerShell/Python files exist? Can we avoid the copy-paste step? | **Answered below** — files are necessary (justification + evidence attached), and the "copy-paste" friction is now fixed at the UI level |
| 2 | Screen-2 actions don't replay; window1/window2 actions should be visibly grouped | **Fixed and GUI-verified** on FileZilla (2 consecutive passing runs) — see below |
| 3 | Is screen-2 element capture itself dropping events? | **Still open** — needs a real re-recording session with logs before I touch the capture code (see below for why) |

---

## 1. PowerShell/Python helper files

**Short answer:** every generated test folder ships 9 small helper
scripts (6 `.ps1` + 3 `.py`, ~850 lines total) because **WinAppDriver's
REST API is missing capabilities the project needs** — no scroll
endpoint, a session can only ever see one window, and its accessibility
bridge can't reach some legacy native controls. Each helper works
around exactly one of those gaps. Full technical breakdown (line
counts, code citations, the `-EncodedCommand` inlining feasibility
analysis) is in `EVIDENCE.md` in this same folder — happy to walk
through it live.

**On the "annoying copy-paste step":** I found the actual cause of that
friction, and it wasn't the helper files themselves — it was a gap in
the UI. `/api/generate` already auto-saves every file (both `.js` tests
+ all the helpers) to `generated-wdio/<AppName>/` on disk and returns
the folder path + a ready-to-run command in its response, but the code
viewer screen never showed that — it just displayed the code with a
"Download" button, so it looked like you had to manually copy the code
into a new file yourself. I've fixed that: the viewer now shows a
persistent banner — `Saved to generated-wdio/<folder>/ — run: npx wdio
run <folder>/wdio.conf.js` with a copy button — right above the code.
No more manual file creation needed; the "copy the script into VS Code"
step you described shouldn't come up anymore.

## 2. Multi-window session replay (window1/window2 grouping)

This was a real gap, not a misunderstanding — this project's own notes
had it flagged as "designed but never built." I implemented it this
session:

- Each recorded window (by its actual OS window handle, not by title
  text — two different windows can share the same title, e.g. an app's
  main window and its own dialog both literally titled the same thing,
  which was actively breaking replay) is now tracked as its own
  segment.
- The generated test now inserts an explicit, separately-logged
  **"switch to window: ..."** step at every point the recording moves
  to a different window — so when you run the replay, the step list
  visibly shows which actions belong to window 1 vs window 2, matching
  what you described wanting in our last call.
- Also fixed a caching bug where, after a dialog closed and the
  recording returned to the main window, replay could keep reusing the
  dead dialog's session instead of reconnecting to the main window —
  this was causing exactly the kind of "second window doesn't replay"
  symptom you saw on FileZilla.

**Update — GUI-verified on FileZilla today.** Re-ran the actual
FileZilla flow (File menu → Site Manager → new site → cancel → new
tab → back to the main window and click controls). Both generated
test variants passed twice in a row. The replay log shows exactly
what I described above working live: menu navigation actually selects
the target item now (`[osExpandCollapse] invoked 'Site Manager...'`),
cross-window clicks land correctly
(`[osScopedInvoke] invoked under main window subtree`), and the
window-switch step shows up in the step list exactly once, only when
actually needed (`[STEP] switch to window: FileZilla`) — no wasted
time on windows that don't need a session. Along the way I found and
fixed three more bugs this surfaced (two pre-existing, one from my own
fix) — happy to share the detailed log if useful, but the short
version is: this is real now, not just passing an automated
regression suite.

**Still open on this item:** the specific case where two different
windows share the exact same literal title (e.g. a main window and
its own dialog both just titled "FileZilla" or "7-Zip") is fixed in
code and covered by the regression suite, but hasn't come up in an
actual recording yet to confirm live — I'll re-check with a 7-Zip-style
app next.

## 3. Screen-2 element capture

I want to be upfront that I haven't confirmed whether this is a real
capture bug yet, separate from the replay issue in #2. There are two
different possible causes — either the recorder is dropping elements
on a newly-opened second window because it hasn't registered that
window yet, or it's a timing issue where the click happens before the
new window is fully ready to be inspected. These need different fixes,
and I don't want to guess and patch the wrong one. My plan is to
re-record a real multi-window session with the recorder's diagnostic
logging on, see which of the two it actually is from the logs, and fix
that specific cause — I'll have this alongside the #2 re-verification.

---

## Bottom line

#1 has a real answer (attached) plus an actual UX fix already shipped.
#2 is fixed and GUI-verified on a real app today. #3 needs one more
recording session with logging before I can say what's actually
happening — I'd rather tell you that honestly now than claim it's
fixed before I've confirmed it. Will follow up with the demo video.
