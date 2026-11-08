.PHONY: i dev stop graph

# Ports the Procfile servers listen on (uvicorn x3, Django, Astro).
DEV_PORTS := 8000,8001,8002,8003,4321

i:
	uv sync
	pnpm --dir apps/frontend install

dev:
	uvx honcho start -e .env

# Kill dev servers left behind when honcho dies without cleaning up (e.g. the
# terminal was closed: SIGHUP kills honcho, but its children run in their own
# sessions and keep their ports). Only listeners are matched, so a browser
# connected to one of these ports is left alone. Also stops the neo4j container
# `make graph` started.
stop:
	@pids=$$(lsof -ti tcp:$(DEV_PORTS) -sTCP:LISTEN); \
	if [ -n "$$pids" ]; then kill $$pids && echo "stopped:" $$pids; else echo "nothing running"; fi
	@# And the Neo4j container from `make graph`. Stopped, not removed: the
	@# loaded graph stays in its volume for the next `docker compose up`.
	@if docker info >/dev/null 2>&1 && [ -n "$$(docker compose ps -q neo4j)" ]; then \
		docker compose stop neo4j; else echo "neo4j not running"; fi

# Start the local Neo4j (see docker-compose.yml) and replace its contents with
# the current gold database. Rerun after a promote to refresh the graph.
# GOLD_URL=sqlite:////path/to/gold.db points it at another gold than ./gold.db
# (or $GOLD_DATABASE_URL).
graph:
	@docker info >/dev/null 2>&1 || { echo "Docker isn't running: start Docker Desktop, then rerun make graph"; exit 1; }
	docker compose up -d --wait neo4j
	uv run composer-ingest export-neo4j $(if $(GOLD_URL),--gold-url $(GOLD_URL))
	@echo "Neo4j Browser: http://localhost:7475  (connect to neo4j://localhost:7688, neo4j / composers)"
