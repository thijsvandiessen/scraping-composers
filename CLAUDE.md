# CI rules

Before opening a PR, run everything CI runs and fix anything that fails —
CI gives no fix-up round trip on this repo (squash-merge, so a failing PR
just blocks). All commands below run from the repo root unless noted.

Run this once before anything else below:

```
uv sync --locked
```

CI uses `--locked` (fails if `uv.lock` is out of sync with a `pyproject.toml`
instead of silently re-resolving it), while a bare `uv run ...` will happily
paper over that drift. Do this first or a stale lockfile can pass locally and
still fail CI.

## Tests (per workspace member)

Each `packages/*` and `apps/*` member is tested independently (its own
`pyproject.toml`, isolated from the others):

```
uv run --directory <member> pytest --rootdir . --cov=<import_name> --cov-report=term-missing
```

Coverage is report-only (no `--cov-fail-under` yet) — a coverage drop won't
fail CI, but a failing test will. Run the full matrix before opening a PR:

```
for m in packages/composer-schema packages/composer-models packages/composer-http packages/composer-bronze \
         packages/composer-scrapers packages/composer-crawler packages/composer-extract \
         packages/composer-warehouse packages/composer-gold packages/composer-config \
         apps/consumer-api apps/admin-api apps/cli apps/dashboard; do
  uv run --directory "$m" pytest --rootdir . -q
done
```

## Types, lint, format

```
uv run pyright              # strict mode, whole workspace
uv run ruff check
uv run ruff format --check
uv run pylint packages apps # scoped to C0302 file-length only (300-line cap; see pyproject.toml)
```

## Dependency audit

```
uv export --frozen --no-emit-workspace --format requirements-txt -o requirements.txt
uvx pip-audit --disable-pip --no-deps -r requirements.txt
rm requirements.txt   # generated file, don't commit it
```

## Frontend (only if `apps/frontend` changed)

```
cd apps/frontend
npm ci
npm run lint
npm run format:check
npm run check
npm run build
```

## Commit messages and PR title

Both must follow [Conventional Commits](https://www.conventionalcommits.org/)
(`feat: ...`, `fix: ...`, `build(deps): ...`, etc.) — enforced by commitlint
(`commitlint.config.mjs`, extends `@commitlint/config-conventional`) on every
commit in the PR, and separately on the PR title itself. The repo
squash-merges, so **the PR title is what becomes the commit message on
`main`** — get the title right even if individual commits are messy.
