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


def _default_values(name: str | None) -> dict[str, Any]:
    """A blank new-crawl form seeded with the CrawlConfig defaults."""
    return {
        "name": name or "",
        "seeds": "",
        "use_sitemap": True,
        "use_common_crawl": False,
        "allow_patterns": "",
        "relevance_query": "",
        "score_threshold": "0.0",
        "follow_links": False,
        "max_depth": "2",
        "max_pages": "",
        "excluded_selector": "",
        "request_delay_s": "0.5",
        "respect_robots": True,
        "extract_kind": "concerts",
    }


def _form_values(request: HttpRequest, name: str | None) -> dict[str, Any]:
    """The submitted form, echoed back so a failed save keeps the input.

    A GET (the new-crawl page) starts from the defaults; only a POST reads the
    checkboxes, whose unchecked state is simply absent from the body.
    """
    if request.method != "POST":
        return _default_values(name)
    return {
        "name": name or request.POST.get("name", "").strip(),
        "seeds": request.POST.get("seeds", ""),
        "use_sitemap": request.POST.get("use_sitemap") == "on",
        "use_common_crawl": request.POST.get("use_common_crawl") == "on",
        "allow_patterns": request.POST.get("allow_patterns", ""),
        "relevance_query": request.POST.get("relevance_query", "").strip(),
        "score_threshold": request.POST.get("score_threshold", "0.0"),
        "follow_links": request.POST.get("follow_links") == "on",
        "max_depth": request.POST.get("max_depth", "2"),
        "max_pages": request.POST.get("max_pages", ""),
        "excluded_selector": request.POST.get("excluded_selector", "").strip(),
        "request_delay_s": request.POST.get("request_delay_s", "0.5"),
        "respect_robots": request.POST.get("respect_robots") == "on",
        "extract_kind": request.POST.get("extract_kind", "concerts"),
    }


def _lines(raw: str) -> list[str]:
    return [line.strip() for line in raw.splitlines() if line.strip()]


def _crawl_payload(values: dict[str, Any]) -> dict[str, Any]:
    """The PUT body for the admin API; raises ValueError on non-numeric input."""
    return {
        "seeds": _lines(values["seeds"]),
        "use_sitemap": values["use_sitemap"],
        "use_common_crawl": values["use_common_crawl"],
        "allow_patterns": _lines(values["allow_patterns"]),
        "relevance_query": values["relevance_query"] or None,
        "score_threshold": float(values["score_threshold"] or "0.0"),
        "follow_links": values["follow_links"],
        "max_depth": int(values["max_depth"] or "2"),
        "max_pages": int(values["max_pages"]) if values["max_pages"].strip() else None,
        "excluded_selector": values["excluded_selector"] or None,
        "request_delay_s": float(values["request_delay_s"] or "0.5"),
        "respect_robots": values["respect_robots"],
        "extract_kind": values["extract_kind"],
    }


def _config_to_values(crawl: dict[str, Any]) -> dict[str, Any]:
    """A stored config as form values (textareas hold one item per line)."""
    return {
        "name": crawl["name"],
        "seeds": "\n".join(crawl["seeds"]),
        "use_sitemap": crawl["use_sitemap"],
        "use_common_crawl": crawl["use_common_crawl"],
        "allow_patterns": "\n".join(crawl["allow_patterns"]),
        "relevance_query": crawl.get("relevance_query") or "",
        "score_threshold": str(crawl["score_threshold"]),
        "follow_links": crawl["follow_links"],
        "max_depth": str(crawl["max_depth"]),
        "max_pages": "" if crawl["max_pages"] is None else str(crawl["max_pages"]),
        "excluded_selector": crawl.get("excluded_selector") or "",
        "request_delay_s": str(crawl["request_delay_s"]),
        "respect_robots": crawl["respect_robots"],
        "extract_kind": crawl.get("extract_kind") or "concerts",
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


def run_crawl_pipeline(request: HttpRequest, name: str) -> HttpResponse:
    """Crawl, extract and load in one go, so the chain needs no babysitting."""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    api = AdminAPI.from_env()
    try:
        started = api.run_crawl_pipeline(name)
    except AdminAPIError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(
            request,
            f"running {started['source']}: crawl → extract → load (snapshot {started['snapshot_id']})",
        )
    return redirect("crawls_index")


def start_extract(request: HttpRequest, name: str) -> HttpResponse:
    """Run the LLM over the crawl's latest snapshot, into a new one."""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    api = AdminAPI.from_env()
    try:
        started = api.start_extract(name)
    except AdminAPIError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, f"extracting {started['source']} → snapshot {started['snapshot_id']}")
    return redirect("crawls_index")


def start_load(request: HttpRequest, name: str) -> HttpResponse:
    """Load the crawl's latest extracted snapshot into the database."""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    api = AdminAPI.from_env()
    try:
        started = api.load_crawl(name)
    except AdminAPIError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, f"loading {started['source']} into the database (run {started['run_id']})")
    return redirect("crawls_index")
