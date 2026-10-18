"""Drive the real CLI end to end, in a throwaway directory, one subprocess per step.

A subprocess per step rather than calling the command functions in-process: each
step then starts as cold as it does in production — settings read from the
environment, databases opened from their files, nothing shared through module
state — which is exactly what an in-process test can't vouch for.
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import Engine, create_engine
from sqlalchemy.pool import NullPool

from .sources import EDITION_ENV, SYNTHETIC_SOURCES

SOURCES = tuple(adapter.name for adapter in SYNTHETIC_SOURCES)


@dataclass(frozen=True)
class Pipeline:
    """A bucket, a silver and a gold database under ``root``, and the CLI over them."""

    root: Path

    @property
    def bucket(self) -> Path:
        return self.root / "bucket"

    @property
    def silver_url(self) -> str:
        return f"sqlite:///{self.root / 'composers.db'}"

    @property
    def gold_url(self) -> str:
        return f"sqlite:///{self.root / 'gold.db'}"

    def silver(self) -> Engine:
        return create_engine(self.silver_url, poolclass=NullPool)

    def gold(self) -> Engine:
        return create_engine(self.gold_url, poolclass=NullPool)

    def cli(self, *args: str, edition: int = 1) -> str:
        """Run one ``composer-ingest`` command; fail the test, with its output, if it fails."""
        env = {
            **os.environ,
            EDITION_ENV: str(edition),
            # Pin every default to this directory too, so a command that ignores
            # a flag can't silently reach the developer's real data.
            "DATABASE_URL": self.silver_url,
            "GOLD_DATABASE_URL": self.gold_url,
            "BUCKET_PATH": str(self.bucket),
            "PAGE_CACHE_ENABLED": "false",
            "SCRAPER_CONTACT_EMAIL": "e2e@example.invalid",
        }
        command = [sys.executable, "-m", "composer_e2e", "--log-level", "warning", *args]
        # cwd: settings also read a .env from the working directory; there is none here.
        result = subprocess.run(command, cwd=self.root, env=env, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            raise AssertionError(
                f"composer-ingest {' '.join(args)} exited {result.returncode}\n"
                f"--- stdout\n{result.stdout}--- stderr\n{result.stderr}"
            )
        return result.stdout

    def ingest(self, edition: int = 1) -> None:
        """One ingestion event: fetch every source into the bucket, then load it into silver."""
        for source in SOURCES:
            self.cli("fetch", source, "--bucket-path", str(self.bucket), edition=edition)
        for source in SOURCES:
            self.cli("process", source, "--bucket-path", str(self.bucket), edition=edition)

    def curate(self) -> str:
        """The silver → gold half: link duplicate people, then promote (which derives
        concerts and recordings first). Returns promote's stats output."""
        self.cli("dedupe-persons")
        return self.cli("promote", "--gold-url", self.gold_url)
