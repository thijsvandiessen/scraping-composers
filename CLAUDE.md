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
- **LLM extraction**: `packages/composer-extract` (`composer-ingest extract`, between `crawl` and `process`) runs a local Ollama model over crawled pages' markdown and emits the standard `WorkMentionDocument` + `EntityDocument` types, so the existing `process → derive_concerts → promote` steps consume them unchanged. Its concert payloads are marked `_source: "llm"`; `derive_concerts()` reads them via a marker branch (`_llm_fields`), so LLM concerts are site-agnostic and need no per-source wiring. Also exposed as `POST /admin/v1/crawls/{name}/extract` and an Extract button on the dashboard's Crawls page.
- **Crawl records store markdown, not HTML**: `CrawlRecord` keeps crawl4ai's pruned `fit_markdown` plus page `metadata`; the raw HTML body is deliberately not persisted (~66x larger, and nothing reads it). Two traps in `composer_crawler/fetch.py`: crawl4ai's `result.markdown` is a `str` *subclass* whose string value is the **unpruned** `raw_markdown`, so `_markdown()` must read the `fit_markdown` attribute first and coerce to a plain `str` (`dataclasses.asdict` turns the subclass back into an unserializable `MarkdownGenerationResult`); and cookie-consent dialogs survive `PruningContentFilter` because they are dense prose, so `run_config()` always excludes the common consent containers (`_CONSENT_SELECTOR`) plus any per-crawl `excluded_selector`.
