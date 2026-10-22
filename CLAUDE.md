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

Each `packages/*` and `apps/*` member, plus `tests/e2e` (the end-to-end
suite), is tested independently (its own `pyproject.toml`, isolated from the
others):

```
uv run --directory <member> pytest --rootdir . --cov=<import_name> --cov-report=term-missing
```

The `--cov` name is the import package, which isn't always the member's name:
`consumer-api` → `composer_api`, `admin-api` → `composer_admin`, `cli` →
`composer_cli`, `dashboard` → `scrapers` and `config` (no `src/` layout),
`tests/e2e` → `composer_e2e`. The e2e figure only measures its harness and
synthetic sources, because the CLI runs in subprocesses; it says nothing about
product-code coverage.

Coverage is report-only (no `--cov-fail-under` yet) — a coverage drop won't
fail CI, but a failing test will. Run the full matrix before opening a PR:

```
for m in packages/composer-schema packages/composer-models packages/composer-http packages/composer-bronze \
         packages/composer-scrapers packages/composer-warehouse packages/composer-gold packages/composer-graph packages/composer-config \
         apps/consumer-api apps/admin-api apps/cli apps/dashboard tests/e2e; do
  uv run --directory "$m" pytest --rootdir . -q
done
```

## Docs follow the tests

Domain docs live with the code: each member has a `README.md` whose **Tests**
section lists its test files and what they cover. The root `README.md` stays a
short overview that links to them. When a PR adds, removes or renames a test
file, or changes what one covers, update that member's Tests section in the
same PR. The same goes for behaviour a README describes.

Check that each Tests section still names every test file:

```
for m in $(ls -d packages/* | grep -v composer-scrapers) \
         apps/consumer-api apps/admin-api apps/cli apps/dashboard tests/e2e; do
  for t in "$m"/tests/test_*.py; do
    grep -q "$(basename "$t")" "$m/README.md" || echo "undocumented: $t"
  done
done
```

`composer-scrapers` is left out because its table marks the standard
`test_<source>.py` / `_adapter` / `_fetch` files with ✓ rather than naming
them. Check its table by eye when adding a source.

## Postgres-backed tests

Five members (`composer-models`, `composer-warehouse`, `composer-gold`,
`admin-api`, `consumer-api`) have tests that need a real Postgres; CI runs them
in a separate `test-postgres` job. They **skip silently** when
`COMPOSER_TEST_POSTGRES_URL` is unset, so the matrix above passes without
Docker — which also means a broken Postgres path looks green locally unless you
set it:

```
docker compose up -d postgres
export COMPOSER_TEST_POSTGRES_URL=postgresql+psycopg://composers:composers@localhost:5433/composers
for m in packages/composer-models packages/composer-warehouse packages/composer-gold \
         apps/admin-api apps/consumer-api; do
  uv run --directory "$m" pytest --rootdir . -q
done
```

No Docker? Any Postgres 16 listening on 5433, with a `composers` database
owned by a `composers` role, works the same way. With the server binaries
installed (on Debian they sit in `/usr/lib/postgresql/16/bin`, not on `PATH`),
run as the `postgres` user: `initdb -U composers --auth=trust -D <dir>`, then
`pg_ctl -D <dir> -o '-p 5433' start`, then `createdb -h localhost -p 5433 -U
composers composers`.

The tests put silver and gold in separate schemas on this one server. In real
use gold has a server of its own (`docker compose up -d postgres-gold`, port
5434, `GOLD_DATABASE_URL`).

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

This job can turn red with no code change: a newly published advisory fails
it on `main` and on every open PR at once. Bump just the affected package with
`uv lock --upgrade-package <name>`, re-run the audit, and land it as its own
`build(deps): ...` PR. If another PR is blocked meanwhile, cherry-pick the same
lock change into it; the cherry-pick adds nothing once the fix is on `main`.

## Frontend (only if `apps/frontend` changed)

```
cd apps/frontend
npm ci
npm run lint
npm run format:check
npm run check
npm run build
```

`format:check` (oxfmt) covers the Markdown files under `apps/frontend` too, so
editing `apps/frontend/README.md` counts as a frontend change. CI's frontend job
runs on every PR whatever it touches.

## Test-writing gotchas

- **Person-model training samples 3,000,000 random pairs.** Anything that calls
  `train()` or `estimate_prior()` (including `composer-ingest person-train`)
  takes a minute or more on a test corpus. Patching `U_SAMPLE_PAIRS` does
  nothing, because it is bound as a default argument when the module loads.
  Swap the function instead, so the real code still runs on a smaller sample:
  `monkeypatch.setattr(training, "sample_non_match_levels",
  functools.partial(sample_non_match_levels, pairs=20_000))`.
- **The automatic person labels need specific record shapes.**
  - `year_conflict` reads the `birth_years`/`death_years` sets, not
    `birth_year`, so a hand-built `PersonRecord` needs both.
  - `dates_corroborated` needs birth and death years backed by at least two
    distinct sources. Ingest the same name from two `FakeSource`s with different
    `name=`.
  - `alias_identity` compares normalized names, which keep word order. The
    alias "Louis van Beethoven" matches an entity labelled exactly that, not
    "Beethoven, Louis".

## Commit messages and PR title

Both must follow [Conventional Commits](https://www.conventionalcommits.org/)
(`feat: ...`, `fix: ...`, `build(deps): ...`, etc.) — enforced by commitlint
(`commitlint.config.mjs`, extends `@commitlint/config-conventional`) on every
commit in the PR, and separately on the PR title itself. The repo
squash-merges, so **the PR title is what becomes the commit message on
`main`** — get the title right even if individual commits are messy.
