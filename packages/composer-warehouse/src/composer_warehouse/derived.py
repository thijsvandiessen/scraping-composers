"""Silver's derived state, refreshed in one place.

Everything here is recomputed from what silver already holds — no source is
asked again — and every caller that publishes or rebuilds silver
(``rebuild_silver``, ``composer-ingest promote``, the admin API's Promote)
runs the same passes in the same order through :func:`refresh_derived`. A pass
added here reaches all of them at once.

Order matters: entity links come first (places, then genres, then ensemble
anchors), then the works a composer's duplicate entities split apart are
folded together (which needs ``dedupe_persons`` to have linked the persons,
so ``rebuild_silver`` runs it first), then work links and the links from each
source's work entity to its work, so the event derivations that read entities
and works see the linked ones.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from composer_models.normalize import dedup_key
from sqlalchemy.orm import Session

from .ensembles import AnchorStats, anchor_ensembles, ensemble_match_key
from .genres import GenreLinkStats, link_label_genres
from .places import LinkPlacesStats, link_places
from .works.entities import WorkEntityStats, link_work_entities
from .works.links import LinkStats, link_works
from .works.merge import MergeStats, merge_works

if TYPE_CHECKING:
    from .concerts import DeriveConcertsStats
    from .recordings import DeriveRecordingsStats


@dataclass
class ParticipantResolver:
    """Shared participant credit resolution for concert and recording derivation."""

    person_by_key: dict[str, uuid.UUID]
    ensemble_by_key: dict[str, uuid.UUID] = field(default_factory=dict[str, uuid.UUID])
    # Anchor names (see :func:`~composer_warehouse.ensembles.alias_index`): the
    # last resort for a credit no source minted an entity for.
    anchor_by_alias: dict[str, uuid.UUID] = field(default_factory=dict[str, uuid.UUID])

    def _resolve(self, role: str, key: str) -> uuid.UUID | None:
        """The entity a credited name refers to.

        Both maps are consulted whatever the role: a credit's slot says how the
        source filed the name, not what the name is — sources list choirs and
        piano trios among the soloists — and ingest kinds a name that reads as
        an ensemble as one (see :func:`~composer_schema.resolve_entity_kind`).
        The role only decides which map is asked first.
        """
        if role == "ensemble":
            return self.ensemble_by_key.get(key) or self.person_by_key.get(key)
        return self.person_by_key.get(key) or self.ensemble_by_key.get(key)

    def _resolve_name(self, role: str, name: str) -> uuid.UUID | None:
        return self._resolve(role, dedup_key(name)) or self.anchor_by_alias.get(ensemble_match_key(name))


@dataclass(frozen=True)
class DerivedStats:
    places: LinkPlacesStats
    genres: GenreLinkStats
    ensembles: AnchorStats
    merged: MergeStats
    works: LinkStats
    work_entities: WorkEntityStats
    concerts: DeriveConcertsStats
    recordings: DeriveRecordingsStats


def refresh_derived(session: Session) -> DerivedStats:
    """Run every derivation pass over silver, in dependency order."""
    from .concerts import derive_concerts
    from .recordings import derive_recordings

    places = link_places(session)
    genres = link_label_genres(session)
    ensembles = anchor_ensembles(session)
    merged = merge_works(session)
    works = link_works(session)
    work_entities = link_work_entities(session)
    concerts = derive_concerts(session)
    recordings = derive_recordings(session)
    return DerivedStats(
        places=places,
        genres=genres,
        ensembles=ensembles,
        merged=merged,
        works=works,
        work_entities=work_entities,
        concerts=concerts,
        recordings=recordings,
    )
