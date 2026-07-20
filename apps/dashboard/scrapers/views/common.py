"""Helpers shared by the dashboard views."""

import math
from typing import Any
from urllib.parse import urlencode


def is_running(item: object) -> bool:
    return isinstance(item, dict) and item.get("status") == "running"


def page_context(page_data: dict[str, Any], base_path: str, params: dict[str, str]) -> dict[str, object]:
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
