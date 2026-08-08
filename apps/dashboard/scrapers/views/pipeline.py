"""Pipeline actions: fetch raw data, load snapshots, promote to gold."""

from django.contrib import admin, messages
from django.http import HttpRequest, HttpResponse, HttpResponseNotAllowed
from django.shortcuts import redirect, render

from ..api import AdminAPI, AdminAPIError
from .common import is_running


def index(request: HttpRequest) -> HttpResponse:
    """Scrapers page: fetch raw data from the sources into the bucket."""
    api = AdminAPI.from_env()
    scrapers: list[dict[str, object]] = []
    error: str | None = None
    try:
        scrapers = api.list_scrapers()
    except AdminAPIError as exc:
        error = str(exc)
    refreshing = any(is_running(s.get("last_snapshot")) for s in scrapers)
    context = {
        **admin.site.each_context(request),
        "title": "Scrapers",
        "scrapers": scrapers,
        "error": error,
        "refreshing": refreshing,
    }
    return render(request, "scrapers/index.html", context)


def start_fetch(request: HttpRequest, name: str) -> HttpResponse:
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    api = AdminAPI.from_env()
    try:
        started = api.fetch_scraper(name)
    except AdminAPIError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, f"fetching {started['source']} → snapshot {started['snapshot_id']}")
    return redirect("scrapers_index")


def fetch_due(request: HttpRequest) -> HttpResponse:
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    api = AdminAPI.from_env()
    try:
        started = api.fetch_due()
    except AdminAPIError as exc:
        messages.error(request, str(exc))
    else:
        if started:
            names = ", ".join(str(s["source"]) for s in started)
            messages.success(request, f"started {len(started)} fetch(es): {names}")
        else:
            messages.info(request, "no scrapers are due")
    return redirect("scrapers_index")


def load_index(request: HttpRequest) -> HttpResponse:
    """Load page: ingest raw bucket snapshots into the database."""
    api = AdminAPI.from_env()
    snapshots: list[dict[str, object]] = []
    runs: list[dict[str, object]] = []
    error: str | None = None
    try:
        snapshots = api.list_snapshots()
        runs = api.list_runs(limit=20)
    except AdminAPIError as exc:
        error = str(exc)
    refreshing = any(is_running(run) for run in runs) or any(is_running(s) for s in snapshots)
    context = {
        **admin.site.each_context(request),
        "title": "Load",
        "snapshots": snapshots,
        "runs": runs,
        "error": error,
        "refreshing": refreshing,
    }
    return render(request, "scrapers/load.html", context)


def process_snapshot(request: HttpRequest, source: str, snapshot_id: str) -> HttpResponse:
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    api = AdminAPI.from_env()
    try:
        started = api.process_snapshot(source, snapshot_id)
    except AdminAPIError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(
            request, f"loading snapshot {snapshot_id} into the database (run {started['run_id']})"
        )
    return redirect("load_index")


def promote_page(request: HttpRequest) -> HttpResponse:
    """Gold status and the button to rebuild it from silver."""
    api = AdminAPI.from_env()
    gold: dict[str, object] | None = None
    error: str | None = None
    try:
        gold = api.gold_status()
    except AdminAPIError as exc:
        error = str(exc)
    refreshing = bool(gold) and gold.get("status") == "running"
    context = {
        **admin.site.each_context(request),
        "title": "Promote",
        "gold": gold,
        "error": error,
        "refreshing": refreshing,
    }
    return render(request, "scrapers/promote.html", context)


PROMOTE_RULES = ("drop_unevidenced_persons", "prune_unreferenced")


def _promote_options(request: HttpRequest) -> dict[str, object]:
    """Promotion options from the promote form; empty means an all-default run.

    Unchecked checkboxes are absent from the POST, so rule toggles are only
    read when the form marker is present — a bare POST stays a default run.
    """
    options: dict[str, object] = {}
    if request.POST.get("options_form"):
        for rule in PROMOTE_RULES:
            if request.POST.get(rule) != "on":
                options[rule] = False
    raw_sitelinks = request.POST.get("min_sitelinks", "").strip()
    if raw_sitelinks:
        options["min_sitelinks"] = int(raw_sitelinks)  # ValueError handled by the view
    raw_appearances = request.POST.get("min_appearances", "").strip()
    if raw_appearances:
        options["min_appearances"] = int(raw_appearances)  # ValueError handled by the view
    raw_referrers = request.POST.get("min_referrers", "").strip()
    if raw_referrers:
        options["min_referrers"] = int(raw_referrers)  # ValueError handled by the view
    gold_path = request.POST.get("gold_path", "").strip()
    if gold_path:
        options["gold_path"] = gold_path
    return options


def start_promote(request: HttpRequest) -> HttpResponse:
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    try:
        options = _promote_options(request)
    except ValueError:
        messages.error(request, "min sitelinks, appearances and referrers must be whole numbers")
        return redirect("promote")
    api = AdminAPI.from_env()
    try:
        api.start_promote(options or None)
    except AdminAPIError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "rebuilding the gold database from silver")
    return redirect("promote")
