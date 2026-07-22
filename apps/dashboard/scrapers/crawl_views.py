"""Crawls pages: manage stored crawl configs and start crawls.

Like every dashboard view, these only talk to the admin API — the configs
file and the bucket belong to the API process, never to Django.
"""

from typing import Any

from django.contrib import admin, messages
from django.http import HttpRequest, HttpResponse, HttpResponseNotAllowed
from django.shortcuts import redirect, render

from .api import AdminAPI, AdminAPIError
from .views.common import is_running

PAGINATION_TYPES = ("none", "page_param", "next_url_from_json")


def crawls_index(request: HttpRequest) -> HttpResponse:
    """Crawls page: the configured crawl targets and their last runs."""
    api = AdminAPI.from_env()
    crawls: list[dict[str, object]] = []
    error: str | None = None
    try:
        crawls = api.list_crawls()
    except AdminAPIError as exc:
        error = str(exc)
    refreshing = any(is_running(c.get("last_snapshot")) for c in crawls)
    context = {
        **admin.site.each_context(request),
        "title": "Crawls",
        "crawls": crawls,
        "error": error,
        "refreshing": refreshing,
    }
    return render(request, "scrapers/crawls.html", context)


def _form_values(request: HttpRequest, name: str | None) -> dict[str, Any]:
    """The submitted form, echoed back so a failed save keeps the input."""
    return {
        "name": name or request.POST.get("name", "").strip(),
        "seeds": request.POST.get("seeds", ""),
        "follow_links": request.POST.get("follow_links") == "on",
        "allow_patterns": request.POST.get("allow_patterns", ""),
        "max_depth": request.POST.get("max_depth", "2"),
        "max_pages": request.POST.get("max_pages", ""),
        "request_delay_s": request.POST.get("request_delay_s", "0.5"),
        "respect_robots": request.POST.get("respect_robots") == "on",
        "pagination_type": request.POST.get("pagination_type", "none"),
        "pagination_param": request.POST.get("pagination_param", "page"),
        "pagination_start": request.POST.get("pagination_start", "1"),
        "pagination_pointer": request.POST.get("pagination_pointer", ""),
    }


def _lines(raw: str) -> list[str]:
    return [line.strip() for line in raw.splitlines() if line.strip()]


def _crawl_payload(values: dict[str, Any]) -> dict[str, Any]:
    """The PUT body for the admin API; raises ValueError on non-numeric input."""
    pagination: dict[str, Any] | None = None
    if values["pagination_type"] == "page_param":
        pagination = {
            "type": "page_param",
            "param": values["pagination_param"].strip() or "page",
            "start": int(values["pagination_start"] or "1"),
        }
    elif values["pagination_type"] == "next_url_from_json":
        pagination = {"type": "next_url_from_json", "pointer": values["pagination_pointer"].strip()}
    payload: dict[str, Any] = {
        "seeds": _lines(values["seeds"]),
        "follow_links": values["follow_links"],
        "allow_patterns": _lines(values["allow_patterns"]),
        "max_depth": int(values["max_depth"] or "2"),
        "max_pages": int(values["max_pages"]) if values["max_pages"].strip() else None,
        "pagination": pagination,
        "request_delay_s": float(values["request_delay_s"] or "0.5"),
        "respect_robots": values["respect_robots"],
    }
    return payload


def _config_to_values(crawl: dict[str, Any]) -> dict[str, Any]:
    """A stored config as form values (textareas hold one item per line)."""
    pagination = crawl.get("pagination") or {}
    return {
        "name": crawl["name"],
        "seeds": "\n".join(crawl["seeds"]),
        "follow_links": crawl["follow_links"],
        "allow_patterns": "\n".join(crawl["allow_patterns"]),
        "max_depth": str(crawl["max_depth"]),
        "max_pages": "" if crawl["max_pages"] is None else str(crawl["max_pages"]),
        "request_delay_s": str(crawl["request_delay_s"]),
        "respect_robots": crawl["respect_robots"],
        "pagination_type": pagination.get("type", "none"),
        "pagination_param": pagination.get("param", "page"),
        "pagination_start": str(pagination.get("start", 1)),
        "pagination_pointer": pagination.get("pointer", ""),
    }


def crawl_form(request: HttpRequest, name: str | None = None) -> HttpResponse:
    """Create (no ``name``) or edit one crawl config."""
    api = AdminAPI.from_env()
    if request.method == "POST":
        values = _form_values(request, name)
        config_name = values["name"]
        try:
            payload = _crawl_payload(values)
            if not config_name:
                raise ValueError("name is required")
        except ValueError:
            messages.error(request, "numeric fields must hold numbers and the crawl needs a name")
        else:
            try:
                api.put_crawl(config_name, payload)
            except AdminAPIError as exc:
                messages.error(request, str(exc))
            else:
                messages.success(request, f"saved crawl {config_name}")
                return redirect("crawls_index")
    elif name is not None:
        try:
            crawl = api.get_crawl(name)
        except AdminAPIError as exc:
            messages.error(request, str(exc))
            return redirect("crawls_index")
        if not crawl["editable"]:
            messages.error(request, f"crawl {name} is code-registered and can't be edited here")
            return redirect("crawls_index")
        values = _config_to_values(crawl)
    else:
        values = _form_values(request, None)
    context = {
        **admin.site.each_context(request),
        "title": f"Edit crawl {name}" if name else "New crawl",
        "is_edit": name is not None,
        "values": values,
    }
    return render(request, "scrapers/crawl_form.html", context)


def delete_crawl(request: HttpRequest, name: str) -> HttpResponse:
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    api = AdminAPI.from_env()
    try:
        api.delete_crawl(name)
    except AdminAPIError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, f"deleted crawl {name}")
    return redirect("crawls_index")


def start_crawl(request: HttpRequest, name: str) -> HttpResponse:
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    api = AdminAPI.from_env()
    try:
        started = api.start_crawl(name)
    except AdminAPIError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, f"crawling {started['source']} → snapshot {started['snapshot_id']}")
    return redirect("crawls_index")
