---
name: daily-status
description: Use when asked for today's tasks, current project status, what's in progress, or open blockers/confirmations for this project — "오늘 할 일", "진행상황", "뭐부터 해야돼", "확인해야 할 거 있어?".
---

# Daily Status Briefing

## Overview
Builds a status briefing for this project from the Obsidian daily notes and
`CLAUDE.md`, instead of re-deriving state from scratch each time. The daily
notes are the source of truth for *why* something is unresolved; `CLAUDE.md`
§4 is the source of truth for the standing backlog.

## When to Use
- "오늘 뭐 해야돼", "진행상황 알려줘", "지금 뭐가 문제야", "컨펌 받아야 할 거 있어?"
- Start of a session, or after a gap, before picking new work.

## When NOT to Use
- User is asking about a specific code bug (use systematic-debugging instead).
- User wants git history, not narrative context (`git log` is authoritative for
  what changed — daily notes are authoritative for *why* and *what's still open*).

## Procedure

1. **Find the latest daily notes.** List
   `C:\hansung\note\project\code-generator\daily\*.md` sorted by date, read the
   **2-3 most recent** files (not just the latest — a decision or blocker often
   spans multiple days, and the newest note frequently says "결정 안 됨,
   다음 세션 최우선" pointing back at an earlier note).
2. **Cross-check `CLAUDE.md` §4 (Current Status) and its "Next actions" list**
   for the standing backlog — items can persist across many sessions.
3. **Cross-check current repo state**, since notes can go stale:
   - `git status` — uncommitted work in progress, matches (or contradicts) what
     the latest note claims was done.
   - `git log --oneline -10` — has anything landed since the last note?
4. **Synthesize into three buckets, most urgent first:**
   - **컨펌/결정 필요** — anything explicitly left as an open decision, an
     unsent draft, or a "다음 세션 논의" item. This is what blocks forward
     progress; surface it before anything else.
   - **오늘 할 일 / 이어서 할 일** — concrete next steps already identified in
     the notes (재검증, 재실행, 미검증 항목).
   - **알려진 문제 / 미해결 갭** — bugs or gaps noted as unconfirmed, flaky, or
     out of scope for now. Don't re-litigate these as new findings — they're
     already tracked.
5. **Flag staleness**: if `git status`/`git log` shows something the notes
   don't mention (new commits, new untracked dirs), say so explicitly rather
   than silently trusting the note.

## Output Format

Lead with 컨펌/결정 필요 (if any exist) — that's usually why the user is asking.
Keep each bucket to short bullets with a one-line "why" for anything non-obvious.
Don't restate the entire note narrative — this is a briefing, not a summary of
the file.

## Common Mistakes
- Reading only the single latest daily note — decisions often get deferred
  across 2-3 sessions ("다음 세션 최우선" chains).
- Treating `CLAUDE.md` §4 as fully current — it's updated "every session" per
  its own header but daily notes can be ahead of it; prefer the daily notes for
  recency, `CLAUDE.md` for the durable backlog.
- Presenting stale unsent drafts/decisions as if already handled.
