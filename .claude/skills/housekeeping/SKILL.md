---
name: housekeeping
description: Weekly repo check-up — open PRs, dependency updates and open issues, plus a short review of this skill itself. Reports only; changes nothing. Use when asked for housekeeping or a repo check-up.
---

# Housekeeping

Report only: don't push, comment, approve, merge, close or label anything.
Repo: `thijsvandiessen/scraping-composers`. Use the GitHub MCP tools
(`mcp__github__*`), not `gh`.

## 1. Open PRs

For each open PR, check CI on the head commit, mergeable state and reviews.
Put it in one bucket:

- **Stuck bump**: a Dependabot PR open for more than a day. Minor and patch
  bumps auto-merge (`.github/workflows/dependabot-auto-merge.yml`), so say
  what blocked it: a failing check (with the root cause from its log), a
  conflict, or a major bump that needs a human.
- **Red or conflicted**: any other PR with a failing check or a conflict.
- **Stale**: no update in 30+ days.
- **Fine**: everything else.

## 2. Dependencies

Dependabot handles updates (`.github/dependabot.yml`).

- Run the dependency audit from `CLAUDE.md`, and `npm audit --omit=dev` in
  `apps/frontend` after `npm ci`.
- For each advisory: package, advisory id, fixed version, and whether an open
  Dependabot PR already bumps it.
- List any bump PR closed without merging in the past week.

## 3. Open issues

For each open issue, read it, then look for merged PRs and commits that
mention it (`git log --grep`, `mcp__github__search_pull_requests`) and check
whether the code it describes still exists and still behaves that way.
Put it in one bucket, with the evidence (PR, commit or `file:line`):

- **Done**: all of it is on `main`; can be closed.
- **Partly done**: a short checklist of what's done and what's left.
- **Obsolete**: the code or premise is gone or replaced.
- **Still needed**: nothing landed yet; give the first step.

If unsure, call it still needed.

## 4. Self-review

Look back at this run and suggest at most three small changes to this file
that would make next week's report more useful: a check that was noisy, one
that missed something, a step that was unclear. Stay within housekeeping
(PRs, dependencies, issues), not code changes. Give each as a one-line
reason plus the exact text to add or remove. Don't edit the file; if
nothing needs changing, say so.

## 5. Report

Start with one line: "Nothing needs attention" or how many items do. Then
one short section per non-empty bucket, most urgent first: stuck bumps,
advisories, red PRs, issues to close, partly done issues, stale PRs,
still-needed issues, fine PRs, and last the self-review. Each entry is one
line with a link and a next step.
