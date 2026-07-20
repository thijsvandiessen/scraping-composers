"""Curated gold pages: people by role, their concerts, and performed works."""

import uuid

from django.contrib import admin
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import render

from ..api import AdminAPIError, DataAPI
from .common import page_context

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
        **page_context(result, request.path, params),
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
        **page_context(result, request.path, {}),
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
        **page_context(result, request.path, params),
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
        **page_context(result, request.path, params),
    }
    return render(request, "scrapers/works.html", context)
