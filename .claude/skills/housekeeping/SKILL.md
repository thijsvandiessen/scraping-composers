---
name: housekeeping
description: Weekly repo check-up — open PRs, dependency updates and open issues, plus a short review of this skill itself. Reports on the repo and changes nothing there; its only write is a draft PR improving this skill. Use when asked for housekeeping or a repo check-up.
---

# Housekeeping

Report only: don't push, comment, approve, merge, close or label anything.
The one exception is the self-review PR in step 4, which touches only this
file. Repo: `thijsvandiessen/scraping-composers`. Use the GitHub MCP tools
(`mcp__github__*`), not `gh`.

## 1. Open PRs

For each open PR, check CI on the head commit, mergeable state and reviews.
Put it in one bucket:

- **Stuck bump**: a Dependabot PR open for more than a day. Minor and patch
  bumps auto-merge (`.github/workflows/dependabot-auto-merge.yml`), so say
  what blocked it: a failing check (with the root cause from its log), a
  conflict, or a major bump that needs a human. Also read the `dependabot`
  job on every bump PR, whatever its age: if it failed, auto-merge was never
  enabled and the PR won't merge itself even once CI is green.
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

List open issues with `fields: ["number","title","updated_at"]` first, then
read each body with `issue_read`; a listing with bodies overflows the tool
output. For each open issue, read it, then look for merged PRs and commits
that mention it (`git log --grep`, `mcp__github__search_pull_requests`) and
check whether the code it describes still exists and still behaves that way.
Start by checking that the packages and files the issue names still exist
(`ls packages apps`); an issue about a removed package is obsolete.
Put it in one bucket, with the evidence (PR, commit or `file:line`):

- **Done**: all of it is on `main`; can be closed.
- **Partly done**: a short checklist of what's done and what's left.
- **Obsolete**: the code or premise is gone or replaced.
- **Still needed**: nothing landed yet; give the first step.

If unsure, call it still needed.

## 4. Self-review

Look back at this run and pick at most three small changes to this file
that would make next week's report more useful: a check that was noisy, one
that missed something, a step that was unclear. Stay within housekeeping
(PRs, dependencies, issues), not code changes. If nothing needs changing,
say so and skip the PR.

Otherwise open a draft PR with them:

- First look for an open PR that already edits this file. If one exists,
  don't open a second: report its link and add only suggestions it doesn't
  already make to the report, not to that PR.
- Branch from the latest `main`. Use the branch the session names, if it
  names one, else `housekeeping/self-review-<YYYY-MM-DD>`.
- Edit only this file. Commit and title the PR as
  `docs(housekeeping): <what changes>` (commitlint checks both).
- In the PR body, list each change with its one-line reason, taken from
  what this run ran into.
- Don't merge it, mark it ready or request reviewers: the owner decides.

## 5. Report

If `main`, the open PRs' head SHAs and the issues' `updated_at` are
unchanged since the last report in this conversation, say so and give only
the counts. Otherwise start with one line: "Nothing needs attention" or how
many items do. Then one short section per non-empty bucket, most urgent
first: stuck bumps, advisories, red PRs, issues to close, partly done
issues, stale PRs, still-needed issues, fine PRs, and last the self-review,
with the link to its PR. Each entry is one line with a link and a next step.
