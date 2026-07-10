# composer-frontend

Public-facing Astro app over the **gold consumer API**: a searchable, paginated
composer list and a detail page where every fact is shown with the source it
came from. Wikidata-backed facts display the QID and link to the exact item
page (e.g. <https://www.wikidata.org/wiki/Q255>), so readers can verify each
piece of information where it was found.

## Running

The app needs the gold consumer API (see the repo README):

```sh
uv run uvicorn composer_api:gold_app --port 8000
```

Then:

```sh
npm install
npm run dev          # http://localhost:4321
npm run check        # astro check (TypeScript)
npm run build        # production build to dist/
npm start            # serve the production build (standalone node server)
```

## Configuration

| Variable       | Default                 | Meaning                        |
| -------------- | ----------------------- | ------------------------------ |
| `GOLD_API_URL` | `http://localhost:8000` | Base URL of the gold consumer API |

## Design notes

- **Server-rendered on purpose** (`output: "server"`, node adapter): `gold.db`
  is atomically swapped by `composer-ingest promote` at arbitrary times and the
  API reads it per request, so pages are rendered per request too. All API
  calls happen in the Astro server process — the browser never talks to
  FastAPI, which is why the consumer API needs **no CORS** configuration. If
  client-side fetching is ever introduced, add `CORSMiddleware` to
  `create_app` in `apps/consumer-api/src/composer_api/main.py` first.
- **Responses are validated with zod** (`src/lib/schemas.ts`): the API exposes
  no types, so the schemas mirror its Pydantic models and `parse()` every
  response — malformed data fails loudly instead of rendering garbage. Objects
  are loose, so additive API changes don't break the frontend.
- No client-side JavaScript: search, sort, and pagination are plain GET forms
  and links.
