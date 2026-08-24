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

## Postgres-backed tests

Three members (`composer-models`, `composer-warehouse`, `admin-api`) have tests
that need a real Postgres; CI runs them in a separate `test-postgres` job. They
**skip silently** when `COMPOSER_TEST_POSTGRES_URL` is unset, so the matrix
above passes without Docker — which also means a broken Postgres path looks
green locally unless you set it:

```
docker compose up -d postgres
export COMPOSER_TEST_POSTGRES_URL=postgresql+psycopg://composers:composers@localhost:5433/composers
for m in packages/composer-models packages/composer-warehouse apps/admin-api; do
  uv run --directory "$m" pytest --rootdir . -q
done
```

## Schema changes

SQLite gets its schema from `create_all` and is rebuilt from bronze; Postgres
is migrated by Alembic. A drift test asserts the two agree, so a model change
without a revision fails CI. Autogenerate **against Postgres** — reflecting
SQLite compares `CHAR(32)` to the model's `Uuid` and proposes a bogus
`alter_column`:

```
docker compose up -d postgres
DATABASE_URL=postgresql+psycopg://composers:composers@localhost:5433/composers \
  uv run alembic revision --autogenerate -m "<what changed>"
uv run ruff format packages/composer-models/src/composer_models/migrations/versions
```

The `ruff format` is not optional: generated revisions routinely exceed the
110-column limit and `ruff format --check` is a CI job.

## Types, lint, format

```
uv run pyright              # strict mode, whole workspace
uv run ruff check
uv run ruff format --check
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
