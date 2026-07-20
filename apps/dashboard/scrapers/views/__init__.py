"""Dashboard views, split by area: pipeline actions, silver data browsing,
and the curated gold pages."""

from . import data, gold, pipeline
from .data import data_overview, entities, entity_detail, review, works
from .gold import concert_detail, concerts_list, gold_works, people, person_concerts
from .pipeline import (
    fetch_due,
    index,
    load_index,
    process_snapshot,
    promote_page,
    start_fetch,
    start_promote,
)

__all__ = [
    "concert_detail",
    "concerts_list",
    "data",
    "data_overview",
    "entities",
    "entity_detail",
    "fetch_due",
    "gold",
    "gold_works",
    "index",
    "load_index",
    "people",
    "person_concerts",
    "pipeline",
    "process_snapshot",
    "promote_page",
    "review",
    "start_fetch",
    "start_promote",
    "works",
]
