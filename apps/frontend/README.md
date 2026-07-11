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
npm run gen          # regenerate the API schemas from OpenAPI (see below)
```

## API types

The response schemas in `src/lib/generated/zod.gen.ts` are **generated** from the
consumer API's OpenAPI document — do not edit them by hand. `src/lib/schemas.ts`
re-exports them under stable names and `src/lib/api.ts` `parse()`s every response.
Both `openapi.json` and the generated code are committed, so `check`/`build`/CI
need neither the codegen nor a running API.

Regenerate after changing the API's Pydantic response models
(`apps/consumer-api/src/composer_api/schemas.py`):

```sh
npm run gen          # dump openapi.json from the app, then regenerate schemas
# individually:
npm run gen:openapi  # uv run python scripts/dump_openapi.py > openapi.json
npm run gen:api      # openapi-ts: openapi.json -> src/lib/generated/
```

`gen:openapi` reads the schema straight from the FastAPI app (no server or
`gold.db` needed). To pull it from a running server instead:

```sh
npx openapi-ts -i http://localhost:8000/openapi.json -o src/lib/generated
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
- **Responses are validated with zod** (`src/lib/schemas.ts`): the schemas are
  generated from the API's OpenAPI document (see [API types](#api-types)) and
  `parse()` every response — malformed data fails loudly instead of rendering
  garbage. Unknown fields are stripped rather than rejected, so additive API
  changes don't break the frontend (re-run `npm run gen` to pick them up).
- No client-side JavaScript: search, sort, and pagination are plain GET forms
  and links.
