"""Helpers shared by the dashboard views."""

import math
from typing import Protocol
from urllib.parse import urlencode

from django.http import HttpRequest

from ..schemas import PaginatedResponse


class _HasStatus(Protocol):
    @property
    def status(self) -> str | None: ...


def is_running(item: _HasStatus | None) -> bool:
    return item is not None and item.status == "running"


def page_number(request: HttpRequest) -> int:
    """The 1-based ``?page=`` parameter; a missing or invalid value means page 1."""
    try:
        return max(1, int(request.GET.get("page", "1") or "1"))
    except ValueError:
        return 1


def page_context(
    page_data: PaginatedResponse | None, base_path: str, params: dict[str, str]
) -> dict[str, object]:
    """Prev/next links and page count for a paginated API response."""
    total = page_data.total if page_data is not None else 0
    page = page_data.page if page_data is not None else 1
    limit = page_data.limit if page_data is not None and page_data.limit > 0 else 20
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
