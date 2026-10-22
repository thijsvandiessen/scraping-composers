---
name: housekeeping
description: Weekly repo check-up — open pull requests and dependency updates. Reports what needs attention; changes nothing. Use when asked for housekeeping, a repo check-up, or the state of open PRs and dependency bumps.
---

# Housekeeping

Report-only. Do not push, comment, approve, merge, close or label anything:
the output is the report at the end, for the maintainer to act on.

Repo: `thijsvandiessen/scraping-composers`. Use the GitHub MCP tools
(`mcp__github__*`), not `gh`.

## 1. Open pull requests

List every open PR. For each, fetch its CI status on the head commit,
mergeable state and reviews, and sort it into one bucket:

- **Stuck dependency bump** — opened by `dependabot[bot]` and still open more
  than a day after it was created. `.github/workflows/dependabot-auto-merge.yml`
  approves and auto-merges minor and patch bumps, so one still open means
  something blocked it. Say which: a failing check (name it, and whether the
  same check fails on `main`), a merge conflict, or a major bump (excluded
  from auto-merge, needs a human). For a failing check, read the job log and
  give the root cause in one line.
- **Red or conflicted** — any other PR whose latest commit has a failing
  check or which can't merge cleanly. Name the check or the conflict.
- **Stale** — no update in 30 days or more. Give its age and the last thing
  that happened (last push, last review).
- **Fine** — green, mergeable, recently active. List these in one line each.

Drafts go in the same buckets but are marked `(draft)`.

## 2. Dependencies

The repo uses Dependabot (`.github/dependabot.yml`: GitHub Actions, uv and
npm, weekly); there is no Renovate.

- Run the dependency audit from `CLAUDE.md` (`uv sync --locked`, then
  `uv export` + `uvx pip-audit`, then delete `requirements.txt`). For any
  advisory, give the package, the advisory id, the fixed version, and whether
  an open Dependabot PR already bumps it.
- For npm, run `npm audit --omit=dev` in `apps/frontend` after `npm ci`, and
  report advisories the same way.
- Note any dependency bump PR that was closed without merging in the past
  week (Dependabot gave up or someone closed it), so a skipped update isn't
  lost.

## 3. Report

Lead with a single line: either "Nothing needs attention" or how many items
do. Then one section per bucket that has entries, most urgent first: stuck
bumps, audit advisories, red or conflicted PRs, stale PRs, fine PRs. Every
entry links the PR or advisory and ends with the concrete next step
("re-run after #NNN lands", "needs a human: major bump of X").
Skip empty sections.
