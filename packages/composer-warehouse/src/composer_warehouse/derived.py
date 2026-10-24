"""Silver's derived state, refreshed in one place.

Everything here is recomputed from what silver already holds — no source is
asked again — and every caller that publishes or rebuilds silver
(``rebuild_silver``, ``composer-ingest promote``, the admin API's Promote)
runs the same passes in the same order through :func:`refresh_derived`. A pass
added here reaches all of them at once.

Order matters: entity links come first, so the event derivations that read
entities see the linked ones.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from .concerts import DeriveConcertsStats, derive_concerts
from .genres import GenreLinkStats, link_label_genres
from .recordings import DeriveRecordingsStats, derive_recordings


@dataclass(frozen=True)
class DerivedStats:
    genres: GenreLinkStats
    concerts: DeriveConcertsStats
    recordings: DeriveRecordingsStats


def refresh_derived(session: Session) -> DerivedStats:
    """Run every derivation pass over silver, in dependency order."""
    genres = link_label_genres(session)
    concerts = derive_concerts(session)
    recordings = derive_recordings(session)
    return DerivedStats(genres=genres, concerts=concerts, recordings=recordings)
