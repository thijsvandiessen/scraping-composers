# Scraping Composers Guidelines

## Commands
- **Linting & Formatting**: `uv run ruff check .` and `uv run ruff format .` (Line length is configured to 110 characters).
- **Type Checking**: `uv run pyright`
- **Testing**: `uv run pytest`

## Commit Standards
- **Conventional Commits**: This repository enforces conventional commits (e.g. `feat: ...`, `fix: ...`, `chore: ...`) via `@commitlint/config-conventional`.

## Architecture & Workflows
- **Ingestion**: The ingestion pipeline lives in `packages/composer-warehouse/src/composer_warehouse/ingestion/core.py`.
- **Deduplication**: Entity deduplication runs as a post-hoc pass in `dedupe_persons()`. Always ensure deduplication keys use `wikidata_id` when available to prevent falsely merging namesakes.
