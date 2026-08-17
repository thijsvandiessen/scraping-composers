"""The composer list this source is scoped to, read straight out of gold.db.

Unlike every other source here, ``imslp_works`` does not discover composers
from IMSLP itself — it only walks IMSLP for composers gold.db already knows
about. ``composer-scrapers`` sits below ``composer-warehouse`` in the
workspace's dependency order, so this reads gold.db directly with stdlib
``sqlite3`` in read-only mode rather than pulling in the warehouse's
SQLAlchemy models.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from urllib.parse import quote


@dataclass(frozen=True)
class GoldComposer:
    """One composer to enrich, as gold.db already knows them.

    ``known_imslp_url`` is a category URL an earlier IMSLP scrape already
    confirmed for this entity (via its ``entity_records`` row), if any.
    """

    entity_id: str
    label: str
    known_imslp_url: str | None


#: Persons whose has_profession claim looks like "composer" (also matches
#: "composer, conductor", "composer/arranger", ...), left-joined against any
#: IMSLP category URL an earlier scrape already confirmed for them.
_QUERY = """
select e.id, e.label,
    (
        select er.url from entity_records er
        join sources s on er.source_id = s.id
        where er.entity_id = e.id and s.name = 'imslp'
        order by er.url limit 1
    ) as known_imslp_url
from entities e
where e.kind = 'person'
  and e.id in (
      select c.subject_id from claims c
      join entities p on c.object_id = p.id
      where c.predicate = 'has_profession' and p.label like '%compos%'
  )
order by e.label
"""


def composers(gold_db_path: str) -> list[GoldComposer]:
    """Every gold person entity whose profession looks like "composer"."""
    uri = f"file:{quote(gold_db_path)}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        rows = conn.execute(_QUERY).fetchall()
    finally:
        conn.close()
    return [GoldComposer(entity_id=row[0], label=row[1], known_imslp_url=row[2]) for row in rows]
