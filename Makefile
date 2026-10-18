.PHONY: dev stop

# Ports the Procfile servers listen on (uvicorn x3, Django, Astro).
DEV_PORTS := 8000,8001,8002,8003,4321

dev:
	uvx honcho start -e .env

# Kill dev servers left behind when honcho dies without cleaning up (e.g. the
# terminal was closed: SIGHUP kills honcho, but its children run in their own
# sessions and keep their ports). Only listeners are matched, so a browser
# connected to one of these ports is left alone.
stop:
	@pids=$$(lsof -ti tcp:$(DEV_PORTS) -sTCP:LISTEN); \
	if [ -n "$$pids" ]; then kill $$pids && echo "stopped:" $$pids; else echo "nothing running"; fi
