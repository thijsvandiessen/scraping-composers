"""Silver data browsing: overview stats, entities, work mentions, works."""

import uuid

from django.contrib import admin
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render

from ..api import AdminAPIError, DataAPI
from .common import page_context


def data_overview(request: HttpRequest) -> HttpResponse:
    """Data overview: dataset counts, per kind / source / mention status."""
    api = DataAPI.silver()
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
    api = DataAPI.silver()
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
        **page_context(result, request.path, params),
    }
    return render(request, "scrapers/entities.html", context)


def entity_detail(request: HttpRequest, entity_id: uuid.UUID) -> HttpResponse:
    """One entity: its claims (with source provenance) and incoming claims."""
    api = DataAPI.silver()
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
            pass  # silver page must render even when gold is down or unpromoted
    context = {
        **admin.site.each_context(request),
        "title": str(entity["label"]) if entity else "Entity",
        "entity": entity,
        "concert_total": concert_total,
        "error": error,
    }
    return render(request, "scrapers/entity_detail.html", context)


MENTION_STATUSES = ("needs_review", "unmatched", "auto_matched", "created", "manual_matched")


def review(request: HttpRequest) -> HttpResponse:
    """Work mentions the matcher wasn't confident about, best candidate first."""
    api = DataAPI.silver()
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
        **page_context(result, request.path, params),
    }
    return render(request, "scrapers/review.html", context)


def works(request: HttpRequest) -> HttpResponse:
    """Searchable resolved-works browser (by title or composer)."""
    api = DataAPI.silver()
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
        **page_context(result, request.path, params),
    }
    return render(request, "scrapers/works.html", context)
