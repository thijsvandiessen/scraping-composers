"""The database schema shared by the silver and gold tiers.

Silver (``composers.db``) and gold (``gold.db``) are the same schema — gold is
a curated copy promoted from silver — so the SQLAlchemy models live here, at
the bottom of the workspace's dependency order, where every tier that reads
either database can import them: the warehouse (which builds silver), the gold
promoter, the consumer API, and the one scraper scoped to gold's composer list.
``db`` holds the engine/session helpers, ``normalize`` the dedup keys and
seeded entity UUIDs that define entity identity in both databases.
"""

from .base import Base, utcnow
from .concerts import Concert, ConcertParticipant, ConcertWork
from .connections import CLAIMS, POOL_SIZE, ConnectionEdge, ConnectionsPayload, EntityConnections
from .core import IngestRun, Source
from .entities import Claim, Entity, EntityRecord
from .persons import PersonMatch
from .programmes import ComposerProgramme, refresh_composer_programmes
from .recordings import Recording, RecordingParticipant, RecordingWork
from .works import RawWorkMention, Work, WorkIdentifier, WorkLink, WorkTitle

__all__ = [
    "CLAIMS",
    "POOL_SIZE",
    "Base",
    "Claim",
    "ComposerProgramme",
    "Concert",
    "ConcertParticipant",
    "ConcertWork",
    "ConnectionEdge",
    "ConnectionsPayload",
    "Entity",
    "EntityConnections",
    "EntityRecord",
    "IngestRun",
    "PersonMatch",
    "RawWorkMention",
    "Recording",
    "RecordingParticipant",
    "RecordingWork",
    "Source",
    "Work",
    "WorkIdentifier",
    "WorkLink",
    "WorkTitle",
    "refresh_composer_programmes",
    "utcnow",
]
