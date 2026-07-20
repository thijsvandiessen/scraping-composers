# Scraping Composers Guidelines

## Commands
- **Linting & Formatting**: `uv run ruff check .` and `uv run ruff format .` (Line length is configured to 110 characters). The frontend uses `npm run lint` (oxlint) and `npm run format` / `npm run format:check` (oxfmt) in `apps/frontend`; generated files are excluded via `ignorePatterns` in `.oxfmtrc.json`.
- **Complexity Limits**: Ruff enforces function complexity at its default thresholds (C901 mccabe ≤ 10, PLR0913 args ≤ 5, PLR0912 branches ≤ 12, PLR0911 returns ≤ 6, PLR0915 statements ≤ 50). Legacy offenders carry a targeted `# noqa` pragma — remove it when refactoring them, never add it to new functions. Class-size rules (e.g. PLR0904) are preview-only in ruff and not enabled; file length is covered by pylint below.
- **File Length Limit**: `uv run pylint packages apps` (max 300 lines per Python file, via C0302 only). Oversized legacy files carry a `# pylint: disable=too-many-lines` pragma — remove it when splitting them, never add it to new files. The frontend enforces the same limit via `npm run lint` (oxlint `max-lines`) in `apps/frontend`.
- **Type Checking**: `uv run pyright`
- **Testing**: `uv run pytest`

## Commit Standards
- **Conventional Commits**: This repository enforces conventional commits (e.g. `feat: ...`, `fix: ...`, `chore: ...`) via `@commitlint/config-conventional`.

## Architecture & Workflows
- **Ingestion**: The ingestion pipeline lives in `packages/composer-warehouse/src/composer_warehouse/ingestion/core.py`.
- **Deduplication**: Entity deduplication runs as a post-hoc pass in `dedupe_persons()`. Always ensure deduplication keys use `wikidata_id` when available to prevent falsely merging namesakes.
