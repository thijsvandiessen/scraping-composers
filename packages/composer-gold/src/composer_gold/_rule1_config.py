"""Rule 1's thresholds: how much concert/recording/composer/sitelink evidence
a person or ensemble needs to survive promotion.

Sourced from a JSON file (``rule1_config.json``, next to this package's
``pyproject.toml`` by default) rather than env vars or CLI flags, so the
numbers can be tuned by editing one file — by hand, or through the admin API's
``/admin/v1/rule1-config`` endpoint, which reads and writes this same file —
without touching code. ``DEFAULT_RULE1_CONFIG_PATH`` assumes the
single-checkout ``uv run`` deployment this repo uses (see the root
``pyproject.toml`` workspace and the ``Procfile``); a packaging change that
ships this module without its package directory would need to pass an
explicit path instead.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

DEFAULT_RULE1_CONFIG_PATH = Path(__file__).resolve().parents[2] / "rule1_config.json"


@dataclass(frozen=True)
class PersonRule1Config:
    min_concert_appearances: int = 1
    min_recording_appearances: int = 1
    # A person who composed a work some source mentioned needs only this many
    # combined concert+recording credits (0 = fully exempt, today's behaviour).
    min_appearances_for_composers: int = 0
    # Wikipedia sitelink count that promotes a person even without the
    # evidence above; None leaves this extra signal off.
    min_sitelinks: int | None = None


@dataclass(frozen=True)
class EnsembleRule1Config:
    min_concert_appearances: int = 1
    min_recording_appearances: int = 1


@dataclass(frozen=True)
class Rule1Config:
    persons: PersonRule1Config = field(default_factory=PersonRule1Config)
    ensembles: EnsembleRule1Config = field(default_factory=EnsembleRule1Config)

    @classmethod
    def from_json(cls, path: str | Path) -> Rule1Config:
        """Load thresholds from ``path``, falling back to defaults (equal to
        the hardcoded defaults this config replaced) if the file is missing or
        not valid JSON — a bad or absent file should degrade gracefully, not
        502 every promote and every ``/admin/v1/rule1-config`` call."""
        try:
            data = json.loads(Path(path).read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            log.warning("rule1 config at %s missing or invalid; using defaults", path)
            return cls()
        return cls(
            persons=PersonRule1Config(**data.get("persons", {})),
            ensembles=EnsembleRule1Config(**data.get("ensembles", {})),
        )

    def write_json(self, path: str | Path) -> None:
        """Write this config to ``path`` as pretty JSON, swapped in atomically
        so a reader never sees a half-written file."""
        data = {"persons": asdict(self.persons), "ensembles": asdict(self.ensembles)}
        target = Path(path)
        tmp_path = target.with_name(target.name + ".tmp")
        tmp_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp_path, target)
