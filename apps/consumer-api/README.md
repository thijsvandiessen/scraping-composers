# consumer-api

Two read-only FastAPI apps with identical routes: gold is the product API,
silver serves the staging data for inspection.

```sh
uv run uvicorn composer_api:gold_app --port 8000     # curated (default)
uv run uvicorn composer_api:silver_app --port 8003   # staging
# open http://localhost:8000/docs
```

- `GET /v1/composers` / `/v1/soloists` / `/v1/conductors` (+ `/{id}`) — searchable, paginated
  people; `?sort=concerts` orders by concert count (each item carries `concert_count`) and
  `?source=` filters by source. Soloists and conductors are picked by profession claim; composers
  by having at least three works (the `composer` profession claim is too loose — Wikidata gives
  it to pop stars and conductors)
- `GET /v1/composers/{id}/works` — a composer's works, with the concerts and catalogues that prove each
- `GET /v1/people/{id}/concerts` / `/v1/people/{id}/recordings` — the concerts a person took part
  in and the recordings they are credited on
- `GET /v1/concerts?q=&source=` / `GET /v1/concerts/{id}` — browse concerts (search by venue or
  participant); the detail carries all musicians (role, discipline, entity link) and the programme.
  Concert and recording ids are UUIDs derived from the source and its own key, so a link stays
  valid across promotes
- `GET /v1/recordings?q=&source=` / `GET /v1/recordings/{id}` — the same for recordings
- `GET /v1/stats` — dataset counts: entities per kind, records per source, works, mention statuses
- `GET /v1/entities?q=&kind=` / `GET /v1/entities/{id}` — search every entity kind; the detail shows
  each claim with its source plus the (capped) list of claims pointing at the entity
- `GET /v1/works?q=&source=` / `GET /v1/works/{id}` — resolved works by title or composer, with
  aliases, mention counts, proof, `identifiers` (catalogue numbers and external ids) and
  `wikidata_id`; the detail adds `links` (the works it is a part, arrangement or version of)
  and `derived` (its own parts, arrangements and versions)
- `GET /v1/mentions?status=` — work mentions with the matcher's decision;
  `status=needs_review` is the review queue, each entry with its best candidate work

Every claim in a detail response carries its provenance: `source` (the scraper
name), `source_url` (the exact page the fact came from, e.g.
`https://www.wikidata.org/wiki/Q255`, falling back to the source homepage), and
`source_external_id` (the source's own id, e.g. the Wikidata QID).

The [frontend](../frontend/README.md) and the [dashboard](../dashboard/README.md)
are both plain HTTP clients of these apps.

## Tests

```sh
uv run --directory apps/consumer-api pytest --rootdir . --cov=composer_api
```

`test_api.py` runs the silver app over seed data in in-memory SQLite, and the
gold app over its promoted copy. Every gold fixture runs twice: promoted into a
SQLite file, and into Postgres when `COMPOSER_TEST_POSTGRES_URL` is set.

| Area | Covers |
| --- | --- |
| People | Soloist, conductor and composer lists: role selection, a person in two roles, search, pagination, the source filter, sorting by concert or recording count, invalid paging (422), detail with claims, and 404s for a missing person or the wrong role. |
| Provenance | `source_url` pointing at the record page or falling back to the source's base URL, in silver and after promotion. |
| Entities and stats | Search across kinds, the kind filter, random sampling, linked claim objects, incoming claims, 404s, and dataset counts. |
| Works and mentions | Works with aliases and mention counts, search by composer, the source filter, work detail with proof and its premiere date and place, identifiers, Wikidata id and part/derived links in gold, the review queue with its candidate, and a composer's works with concert and catalogue proof. |
| Concerts and recordings | Lists newest first with summaries, search by participant and source, details with participants, programme and works, and a person's concerts and recordings. |
| Gold curation | Gold hiding people without performance evidence, keeping works with their mention counts, and stats reflecting curation. |
