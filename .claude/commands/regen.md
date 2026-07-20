---
description: Regenerate a recorded app's WebdriverIO test from current server events and sanity-check the selectors (read-only)
argument-hint: "[AppName]  (default: FileZilla)"
---

Regenerate and verify the generated WebdriverIO test for a recorded desktop app.
App name = `$1` (if empty, use **FileZilla**).

Use **PowerShell** for the HTTP calls (the Bash/Git-Bash tool is unreliable in
this environment). Do NOT edit any files — this command is read-only verification.

Steps:

1. `GET http://localhost:3002/api/status`. If it errors, or `agentOnline` is
   false, STOP and tell me to start the server (`cd server; node server.js`) —
   do not try to start it yourself.

2. `GET /api/events`. Report the event count and a compact one-line-per-event
   summary: `index: action name='…' id='…'`. Flag right away if the recording
   looks messy (stray status-bar/background clicks, repeated menu opens, repeated
   OK-that-triggers-an-error-dialog).

3. `POST /api/generate` with `{ "appName": "<App>", "platform": "Windows",
   "exePath": <known exe or omit> }`. Known exePaths:
   - FileZilla → `C:\Program Files\FileZilla FTP Client\filezilla.exe`
   - (omit `exePath` for others; the server falls back to sessionInfo.)

4. Read `generated-wdio/<App>/<App>TestById.js` and report:
   - **Menu / expandCollapse steps** — verify each menu trigger is paired with an
     item that actually belongs to it (the 편집/보기 mis-merge class of bug).
   - **Reused-automationId disambiguation** — confirm any id shared across fields
     renders as `//<Tag>[@AutomationId="X" and @Name="Y"]`, never bare `~X`.
   - **Noise / risky selectors** — bare `//*[@ClassName="Edit"]`,
     `_failures.push('…:no-selector')`, status-bar / dialog-background clicks,
     or clicks on transient controls (rename edit boxes, error-dialog OK buttons
     that only exist when validation fails).
   - **Window structure** — list the `[Wn]` segments and the `switch to window:`
     steps.

5. Summarize **what looks correct vs. what will likely fail at replay**, and tell
   me the exact command to run it myself: `cd generated-wdio/<App> && node
   <App>TestById.js` — plus which STEP numbers to watch to confirm the intended
   behavior. Do NOT run `node <file>.js` yourself (it needs the live GUI).
