---
name: housekeeping
description: Weekly repo check-up — open PRs, dependency updates and open issues, plus a short review of this skill itself. Reports on the repo and changes nothing there; its only write is a draft PR improving this skill. Use when asked for housekeeping or a repo check-up.
---

# Housekeeping

Report only: don't push, comment, approve, merge, close or label anything.
The one exception is the self-review PR in step 4, which touches only this
file. Repo: `thijsvandiessen/scraping-composers`. Talk to GitHub only
through `gh api` (REST), not the GitHub MCP tools or other `gh` commands:
cloud sessions block GraphQL, so `gh pr list`, `gh pr create` and the like
fail. If `gh api` fails, stop and say so; don't produce a partial report.
Use `--jq` to keep only the fields you need, so listings stay small.

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

For a failing check, read the log of the failed job itself
(`gh api .../actions/jobs/<id>/logs`) and grep it for `##[error]`: the
log's tail is usually checkout cleanup, not the failure.

## 2. Dependencies

Dependabot handles updates (`.github/dependabot.yml`).

- Run the dependency audit from `CLAUDE.md`, and `npm audit --omit=dev` in
  `apps/frontend` after `npm ci`.
- List the open Dependabot alerts
  (`gh api 'repos/{owner}/{repo}/dependabot/alerts?state=open&per_page=100'`).
  They cover more than the audits: dev and transitive dependencies, and
  every lockfile in the repo, including ones CI never installs from. For an
  alert in a lockfile nothing uses, say so: the fix is deleting the file.
  If this one call is refused (it needs Dependabot alerts read access),
  say so in the report and carry on with the rest.
- For each advisory or alert: package, advisory id, severity, fixed version,
  the lockfile, and whether an open Dependabot PR already bumps it.
- List any bump PR closed without merging in the past week.

## 3. Open issues

List open issues with `gh api --paginate repos/{owner}/{repo}/issues` and
drop entries with a `pull_request` key. Keep only number, title and
`updated_at` first; a listing with bodies overflows the tool output (100k+
characters at 30 issues). With more than about ten issues, write the
bodies to a file and hand it to a subagent to triage; otherwise read each
with `gh api .../issues/<n>`. The cloud checkout is shallow, so run
`git fetch --unshallow` before searching history. For each open issue, read
it, then look for merged PRs and commits that mention it (`git log --grep`,
`gh api 'search/issues?q=repo:{owner}/{repo}+is:pr+is:merged+<n>'`) and
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
  `docs(housekeeping): <what changes>` (commitlint checks both). Push with
  `git push`, then open it with `gh api repos/{owner}/{repo}/pulls -f
  draft=true -f head=<branch> -f base=main -f title=... -f body=...`.
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
