"""A composer page's works table: one work mention per listed composition.

Rows are ``<tr class="result">`` with the title in a ``prdName`` cell (linking
to the work's production page) and the opus number in a separate ``prdOpus``
cell. The whole catalogue is inline — the "Sort by" links only re-order it
over AJAX — so one request per composer yields every work.

The title is passed through verbatim and the opus is kept in ``raw`` only,
*not* folded into the title. Folding it in looks tempting — the work matcher
(``composer_warehouse.works.match``) reads the title string alone and treats a
matching opus as near-proof of identity — but this source is a catalogue, not a
programme, so one composer's works routinely share an opus number ("Trio Sonata
Op. 5, No. 1..6", "12 Duette, op.576" vs "Abendfrieden ..., op.576/9"). The
matcher only rejects a same-opus pair when it parsed a work *number* from both
titles, so an appended opus silently auto-merged distinct works (98 of them in a
60-composer trial). Cross-source opus matching still works whenever the site's
own title text spells the opus out, as it often does.
"""

from __future__ import annotations

import html
import re
import uuid

from .. import SourceWorkMention

_ROW = re.compile(r'<tr class="result".*?</tr>', re.S)
_LINKED_TITLE = re.compile(r'<td class="prdName".*?<a[^>]*href="(/en/production/(\d+))"[^>]*>(.*?)</a>', re.S)
_TITLE_CELL = re.compile(r'<td class="prdName".*?>(.*?)</td>', re.S)
_OPUS_CELL = re.compile(r'<td class="prdOpus".*?>(.*?)</td>', re.S)
_TAG = re.compile(r"<[^>]+>")

# Rows without a production link give us no id of the source's own, so one is
# derived deterministically from the composer and the title — re-fetching the
# page yields the same id, keeping the load idempotent.
_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "https://classical-music-online.net")


def _stable_id(*seed: str) -> str:
    """A deterministic UUIDv5 seeded by ``seed`` (joined with a NUL separator so
    distinct part boundaries can't collide, e.g. ("ab", "c") vs ("a", "bc"))."""
    return str(uuid.uuid5(_NAMESPACE, "\x00".join(seed)))


def _text(fragment: str) -> str:
    return html.unescape(re.sub(r"\s+", " ", _TAG.sub("", fragment))).strip()


def _cell(pattern: re.Pattern[str], row: str) -> str:
    match = pattern.search(row)
    return _text(match.group(1)) if match else ""


def iter_work_mentions(
    page: str, composer_name: str, composer_id: str, base_url: str = ""
) -> list[SourceWorkMention]:
    """Parse a composer page's works table into work mentions."""
    mentions: list[SourceWorkMention] = []
    for row in _ROW.findall(page):
        linked = _LINKED_TITLE.search(row)
        path, production_id = (linked.group(1), linked.group(2)) if linked else (None, None)
        title = _text(linked.group(3)) if linked else _cell(_TITLE_CELL, row)
        if not title:
            continue
        opus = _cell(_OPUS_CELL, row)
        mentions.append(
            SourceWorkMention(
                external_id=production_id or _stable_id(composer_id, title),
                title=title,
                composer=composer_name,
                raw={
                    "title": title,
                    "opus": opus or None,
                    "composer": composer_name,
                    "composer_id": composer_id,
                    "production_id": production_id,
                    "url": base_url + path if path else None,
                },
            )
        )
    return mentions
