"""Loading the NY Phil program archive (Kaggle dataset, kagglehub-cached).

Dataset version 3 holds 13,954 programs from the 1842-43 season through
2016-17. The whole source is one download; there is no pagination.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import kagglehub

BASE_URL = "https://www.kaggle.com/datasets/nyphil/perf-history"

DATASET = "nyphil/perf-history"
RAW_FILE = "raw_nyc_phil.json"


def _load_programs() -> list[dict[str, Any]]:
    path = Path(kagglehub.dataset_download(DATASET)) / RAW_FILE
    with open(path, encoding="utf-8") as handle:
        programs: list[dict[str, Any]] = json.load(handle)["programs"]
    return programs
