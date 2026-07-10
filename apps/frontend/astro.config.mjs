// @ts-check
import node from "@astrojs/node";
import { defineConfig } from "astro/config";

// Server-rendered on purpose: gold.db is atomically swapped by `composer-ingest
// promote` at arbitrary times and the API reads it per request, so pages must be
// rendered per request too. All API calls happen in this server process, which is
// also why the consumer API needs no CORS.
export default defineConfig({
  output: "server",
  adapter: node({ mode: "standalone" }),
});
