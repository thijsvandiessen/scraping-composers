import math
import uuid
from typing import Any
from urllib.parse import urlencode

from django.contrib import admin, messages
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseNotAllowed
from django.shortcuts import redirect, render

from .api import AdminAPI, AdminAPIError, DataAPI


def _is_running(item: object) -> bool:
    return isinstance(item, dict) and item.get("status") == "running"


def index(request: HttpRequest) -> HttpResponse:
    """Scrapers page: fetch raw data from the sources into the bucket."""
    api = AdminAPI.from_env()
    scrapers: list[dict[str, object]] = []
    error: str | None = None
    try:
        scrapers = api.list_scrapers()
    except AdminAPIError as exc:
        error = str(exc)
    refreshing = any(_is_running(s.get("last_snapshot")) for s in scrapers)
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
    refreshing = any(_is_running(run) for run in runs) or any(_is_running(s) for s in snapshots)
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


def _page_context(page_data: dict[str, Any], base_path: str, params: dict[str, str]) -> dict[str, object]:
    """Prev/next links and page count for a paginated API response."""
    total = int(page_data.get("total", 0) or 0)
    page = int(page_data.get("page", 1) or 1)
    limit = int(page_data.get("limit", 20) or 20)
    pages = max(1, math.ceil(total / limit))

    def url_for(target: int) -> str:
        return base_path + "?" + urlencode({**params, "page": target})

    return {
        "total": total,
        "page": page,
        "pages": pages,
        "prev_url": url_for(page - 1) if page > 1 else None,
        "next_url": url_for(page + 1) if page < pages else None,
    }


def data_overview(request: HttpRequest) -> HttpResponse:
    """Data overview: dataset counts, per kind / source / mention status."""
    api = DataAPI.bronze()
    stats: dict[str, object] | None = None
    error: str | None = None
    try:
        stats = api.stats()
    except AdminAPIError as exc:
        error = str(exc)
    context = {
        **admin.site.each_context(request),
        "title": "Data overview",
        "stats": stats,
        "error": error,
    }
    return render(request, "scrapers/data_overview.html", context)


def entities(request: HttpRequest, kind: str | None = None) -> HttpResponse:
    """Searchable entity browser; with ``kind`` in the path, one kind's page."""
    api = DataAPI.bronze()
    q = request.GET.get("q", "").strip()
    if kind is None:
        kind = request.GET.get("kind", "").strip()
    order = "random" if request.GET.get("order") == "random" else "label"
    page = int(request.GET.get("page", "1") or "1")
    result: dict[str, object] = {}
    kind_counts: dict[str, int] = {}
    error: str | None = None
    try:
        result = api.list_entities(q=q or None, kind=kind or None, page=page, order=order)
        kind_counts = api.stats()["entities_by_kind"]
    except AdminAPIError as exc:
        error = str(exc)
    params = {key: value for key, value in (("q", q), ("order", order)) if value and value != "label"}
    context = {
        **admin.site.each_context(request),
        "title": f"Entities: {kind}" if kind else "Entities",
        "items": result.get("items", []),
        "q": q,
        "kind": kind,
        "kind_counts": kind_counts,
        "order": order,
        "error": error,
        **_page_context(result, request.path, params),
    }
    return render(request, "scrapers/entities.html", context)


def entity_detail(request: HttpRequest, entity_id: uuid.UUID) -> HttpResponse:
    """One entity: its claims (with source provenance) and incoming claims."""
    api = DataAPI.bronze()
    entity: dict[str, object] | None = None
    error: str | None = None
    try:
        entity = api.get_entity(str(entity_id))
    except AdminAPIError as exc:
        error = str(exc)
    concert_total: int | None = None
    if entity is not None and entity.get("kind") == "person":
        try:
            concert_total = int(DataAPI.gold().person_concerts(str(entity_id), limit=1)["total"])
        except (AdminAPIError, KeyError, TypeError, ValueError):
            pass  # bronze page must render even when gold is down or unpromoted
    context = {
        **admin.site.each_context(request),
        "title": str(entity["label"]) if entity else "Entity",
        "entity": entity,
        "concert_total": concert_total,
        "error": error,
    }
    return render(request, "scrapers/entity_detail.html", context)


def promote_page(request: HttpRequest) -> HttpResponse:
    """Gold status and the button to rebuild it from bronze."""
    api = AdminAPI.from_env()
    gold: dict[str, object] | None = None
    error: str | None = None
    try:
        gold = api.gold_status()
    except AdminAPIError as exc:
        error = str(exc)
    refreshing = bool(gold) and gold is not None and gold.get("status") == "running"
    context = {
        **admin.site.each_context(request),
        "title": "Promote",
        "gold": gold,
        "error": error,
        "refreshing": refreshing,
    }
    return render(request, "scrapers/promote.html", context)


def start_promote(request: HttpRequest) -> HttpResponse:
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    api = AdminAPI.from_env()
    try:
        api.start_promote()
    except AdminAPIError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "rebuilding the gold database from bronze")
    return redirect("promote")


PEOPLE_ROLES = ("composers", "soloists", "conductors")


def people(request: HttpRequest, role: str) -> HttpResponse:
    """People by role, from the curated gold database."""
    if role not in PEOPLE_ROLES:
        raise Http404(f"unknown role {role!r}")
    api = DataAPI.gold()
    q = request.GET.get("q", "").strip()
    sort = "concerts" if request.GET.get("sort") == "concerts" else "label"
    page = int(request.GET.get("page", "1") or "1")
    result: dict[str, object] = {}
    error: str | None = None
    try:
        result = api.list_people(role, q=q or None, page=page, sort=sort)
    except AdminAPIError as exc:
        error = f"{exc} — is the gold API running, and has the gold database been promoted?"
    params = {key: value for key, value in (("q", q), ("sort", sort)) if value and value != "label"}
    context = {
        **admin.site.each_context(request),
        "title": role.capitalize(),
        "items": result.get("items", []),
        "role": role,
        "roles": PEOPLE_ROLES,
        "q": q,
        "sort": sort,
        "error": error,
        **_page_context(result, request.path, params),
    }
    return render(request, "scrapers/people.html", context)


def person_concerts(request: HttpRequest, person_id: uuid.UUID, role: str | None = None) -> HttpResponse:
    """The concerts one person took part in, from gold.

    Reachable with a role segment (back-link to that role's page) or without
    (linked from the entity detail page; back-link to the entity).
    """
    if role is not None and role not in PEOPLE_ROLES:
        raise Http404(f"unknown role {role!r}")
    api = DataAPI.gold()
    page = int(request.GET.get("page", "1") or "1")
    result: dict[str, object] = {}
    error: str | None = None
    try:
        result = api.person_concerts(str(person_id), page=page)
    except AdminAPIError as exc:
        error = str(exc)
    context = {
        **admin.site.each_context(request),
        "title": f"Concerts — {result.get('person_label', '')}",
        "person_label": result.get("person_label"),
        "person_id": person_id,
        "role": role,
        "items": result.get("items", []),
        "error": error,
        **_page_context(result, request.path, {}),
    }
    return render(request, "scrapers/concerts.html", context)


def concerts_list(request: HttpRequest) -> HttpResponse:
    """Browse the derived concerts in gold, newest first."""
    api = DataAPI.gold()
    q = request.GET.get("q", "").strip()
    page = int(request.GET.get("page", "1") or "1")
    result: dict[str, object] = {}
    error: str | None = None
    try:
        result = api.list_concerts(q=q or None, page=page)
    except AdminAPIError as exc:
        error = f"{exc} — is the gold API running, and has the gold database been promoted?"
    params = {"q": q} if q else {}
    context = {
        **admin.site.each_context(request),
        "title": "Concerts",
        "items": result.get("items", []),
        "q": q,
        "error": error,
        **_page_context(result, request.path, params),
    }
    return render(request, "scrapers/concerts_list.html", context)


def concert_detail(request: HttpRequest, concert_id: int) -> HttpResponse:
    """One concert: participants and its programme, from gold."""
    api = DataAPI.gold()
    concert: dict[str, object] | None = None
    error: str | None = None
    try:
        concert = api.get_concert(concert_id)
    except AdminAPIError as exc:
        error = str(exc)
    context = {
        **admin.site.each_context(request),
        "title": f"Concert {concert_id}",
        "concert": concert,
        "error": error,
    }
    return render(request, "scrapers/concert_detail.html", context)


MENTION_STATUSES = ("needs_review", "unmatched", "auto_matched", "created", "manual_matched")


def review(request: HttpRequest) -> HttpResponse:
    """Work mentions the matcher wasn't confident about, best candidate first."""
    api = DataAPI.bronze()
    status = request.GET.get("status", "needs_review").strip()
    page = int(request.GET.get("page", "1") or "1")
    result: dict[str, object] = {}
    error: str | None = None
    try:
        result = api.list_mentions(status=status or None, page=page)
    except AdminAPIError as exc:
        error = str(exc)
    params = {"status": status} if status else {}
    context = {
        **admin.site.each_context(request),
        "title": "Work mentions",
        "items": result.get("items", []),
        "status": status,
        "statuses": MENTION_STATUSES,
        "error": error,
        **_page_context(result, request.path, params),
    }
    return render(request, "scrapers/review.html", context)


def works(request: HttpRequest) -> HttpResponse:
    """Searchable resolved-works browser (by title or composer)."""
    api = DataAPI.bronze()
    q = request.GET.get("q", "").strip()
    page = int(request.GET.get("page", "1") or "1")
    result: dict[str, object] = {}
    error: str | None = None
    try:
        result = api.list_works(q=q or None, page=page)
    except AdminAPIError as exc:
        error = str(exc)
    params = {"q": q} if q else {}
    context = {
        **admin.site.each_context(request),
        "title": "Works",
        "items": result.get("items", []),
        "q": q,
        "error": error,
        **_page_context(result, request.path, params),
    }
    return render(request, "scrapers/works.html", context)


def gold_works(request: HttpRequest) -> HttpResponse:
    """Performed works from the curated gold database."""
    api = DataAPI.gold()
    q = request.GET.get("q", "").strip()
    sort = "mentions" if request.GET.get("sort") == "mentions" else "label"
    page = int(request.GET.get("page", "1") or "1")
    result: dict[str, object] = {}
    error: str | None = None
    try:
        result = api.list_works(q=q or None, page=page, performed_only=True, sort=sort)
    except AdminAPIError as exc:
        error = f"{exc} — is the gold API running, and has the gold database been promoted?"
    params = {key: value for key, value in (("q", q), ("sort", sort)) if value and value != "label"}
    context = {
        **admin.site.each_context(request),
        "title": "Works",
        "items": result.get("items", []),
        "q": q,
        "sort": sort,
        "error": error,
        **_page_context(result, request.path, params),
    }
    return render(request, "scrapers/works.html", context)
