import { defineConfig } from "@hey-api/openapi-ts";

// Generate zod schemas from the committed OpenAPI document (dumped from the gold
// consumer API — see scripts/dump_openapi.py). The frontend validates every API
// response with these at the boundary; src/lib/schemas.ts re-exports them under
// stable names. Regenerate with `npm run gen`.
export default defineConfig({
  input: "./openapi.json",
  output: {
    path: "./src/lib/generated",
    // Generated code is committed; keep output stable without requiring a
    // formatter/linter to be installed in the environment.
    postProcess: [],
  },
  plugins: [
    {
      name: "zod",
      // The API serializes `datetime` fields as naive, timezone-less ISO strings
      // (e.g. "2026-07-01T18:14:05.546628"). Accept those (local) as well as
      // offset-qualified ones, so parse() validates real responses instead of
      // rejecting every one. Matches the old hand-written schema, which typed
      // these as plain strings.
      dates: { local: true, offset: true },
    },
  ],
});
