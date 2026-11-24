"""Loading the NY Phil program archive (Kaggle dataset, kagglehub-cached).

Dataset version 3 holds 13,954 programs from the 1842-43 season through
2016-17. The whole source is one download; there is no pagination.

The one source here that is not HTTP: kagglehub fetches and caches the dataset
itself, so the adapter is a plain :class:`~composer_schema.SourceAdapter` rather
than an :class:`~composer_scrapers.HttpSourceAdapter`. It keeps the same layout —
this module is its fetch — and, the dataset being a closed archive, ``STATIC``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import kagglehub

BASE_URL = "https://www.kaggle.com/datasets/nyphil/perf-history"

DATASET = "nyphil/perf-history"
RAW_FILE = "raw_nyc_phil.json"


def load_programs() -> list[dict[str, Any]]:
    dataset_dir = cast(str, kagglehub.dataset_download(DATASET))  # pyright: ignore[reportUnknownMemberType]
    path = Path(dataset_dir) / RAW_FILE
    with open(path, encoding="utf-8") as handle:
        programs: list[dict[str, Any]] = json.load(handle)["programs"]
    return programs


_load_programs = load_programs
