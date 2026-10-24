"""Silver's derived state, refreshed in one place.

Everything here is recomputed from what silver already holds — no source is
asked again — and every caller that publishes or rebuilds silver
(``rebuild_silver``, ``composer-ingest promote``, the admin API's Promote)
runs the same passes in the same order through :func:`refresh_derived`. A pass
added here reaches all of them at once.

Order matters: entity links come first (places, then genres, then ensemble
anchors), so the event derivations that read entities see the linked ones.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from .concerts import DeriveConcertsStats, derive_concerts
from .ensembles import AnchorStats, anchor_ensembles
from .genres import GenreLinkStats, link_label_genres
from .places import LinkPlacesStats, link_places
from .recordings import DeriveRecordingsStats, derive_recordings


@dataclass(frozen=True)
class DerivedStats:
    places: LinkPlacesStats
    genres: GenreLinkStats
    ensembles: AnchorStats
    concerts: DeriveConcertsStats
    recordings: DeriveRecordingsStats


def refresh_derived(session: Session) -> DerivedStats:
    """Run every derivation pass over silver, in dependency order."""
    places = link_places(session)
    genres = link_label_genres(session)
    ensembles = anchor_ensembles(session)
    concerts = derive_concerts(session)
    recordings = derive_recordings(session)
    return DerivedStats(
        places=places, genres=genres, ensembles=ensembles, concerts=concerts, recordings=recordings
    )
