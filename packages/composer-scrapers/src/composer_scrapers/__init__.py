"""Data sources. Every source is a package of the same shape (see :mod:`.base`):
``fetch.py`` holds its requests, parse modules turn bodies into records, and
``__init__.py`` holds an :class:`HttpSourceAdapter` subclass implementing
``scrape(session, max_pages=None) -> Iterator[EntityDocument | WorkMentionDocument]``
against a :class:`~composer_http.SourceSession`. Register new sources in REGISTRY
to make them available to the CLI.

The document/adapter contracts live in :mod:`composer_schema` and are re-exported
here so each source can keep importing them from its parent package.
"""

from __future__ import annotations

from composer_schema import (
    EntityDocument,
    RefreshCadence,
    ScrapedDocument,
    SourceAdapter,
    SourceClaim,
    WorkMentionDocument,
    is_due,
)

from .base import HttpSourceAdapter
from .records import SourceRecord, SourceWorkMention

# isort: split

from .baerenreiter import BaerenreiterAdapter
from .berlinphil import BerlinPhilAdapter
from .boosey import BooseyAdapter
from .classicalmusiconline import ClassicalMusicOnlineAdapter
from .classicapedia import ClassicapediaAdapter
from .classicfm import ClassicFmAdapter
from .concertgebouw import ConcertgebouwAdapter
from .decca import DeccaAdapter
from .harmoniamundi import HarmoniaMundiAdapter
from .henle import HenleAdapter
from .imslp import ImslpAdapter
from .imslp_recordings import ImslpRecordingsAdapter
from .imslp_works import ImslpWorksAdapter
from .laphil import LaPhilAdapter
from .nyphil import NyPhilAdapter
from .openopus import OpenOpusAdapter
from .rco import RcoAdapter
from .roh import RohAdapter
from .wienerphil import WienerPhilAdapter
from .wikidata import (
    WikidataAdapter,
    WikidataCatalogueAdapter,
    WikidataEnsemblesAdapter,
    WikidataGenresAdapter,
    WikidataInstrumentationAdapter,
    WikidataPlacesAdapter,
    WikidataWorksAdapter,
)

__all__ = [
    "REGISTRY",
    "EntityDocument",
    "HttpSourceAdapter",
    "RefreshCadence",
    "ScrapedDocument",
    "SourceAdapter",
    "SourceClaim",
    "SourceRecord",
    "SourceWorkMention",
    "WorkMentionDocument",
    "is_due",
    "source_base_url",
]

REGISTRY: dict[str, SourceAdapter] = {
    "imslp": ImslpAdapter(),
    "imslp_works": ImslpWorksAdapter(),
    "imslp_recordings": ImslpRecordingsAdapter(),
    "wikidata": WikidataAdapter(),
    "wikidata_works": WikidataWorksAdapter(),
    "wikidata_places": WikidataPlacesAdapter(),
    "wikidata_ensembles": WikidataEnsemblesAdapter(),
    "wikidata_genres": WikidataGenresAdapter(),
    "wikidata_catalogue": WikidataCatalogueAdapter(),
    "wikidata_instrumentation": WikidataInstrumentationAdapter(),
    "concertgebouw_archive": ConcertgebouwAdapter(),
    "nyphil": NyPhilAdapter(),
    "berlinphil": BerlinPhilAdapter(),
    "rco": RcoAdapter(),
    "openopus": OpenOpusAdapter(),
    "classicalmusiconline": ClassicalMusicOnlineAdapter(),
    "classicapedia": ClassicapediaAdapter(),
    "boosey": BooseyAdapter(),
    "baerenreiter": BaerenreiterAdapter(),
    "henle": HenleAdapter(),
    "classicfm": ClassicFmAdapter(),
    "wienerphil": WienerPhilAdapter(),
    "laphil": LaPhilAdapter(),
    "decca": DeccaAdapter(),
    "harmoniamundi": HarmoniaMundiAdapter(),
    "roh": RohAdapter(),
}


def source_base_url(source: str) -> str:
    """Base URL for a bucket source: a registered scraper's, else empty.

    Empty for a source with no adapter — the bucket keeps data for sources whose
    adapter was later removed, and ``rebuild-silver`` replays those too.
    """
    adapter = REGISTRY.get(source)
    return adapter.base_url if adapter is not None else ""
