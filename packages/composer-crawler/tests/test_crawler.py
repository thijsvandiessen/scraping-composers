from collections.abc import Callable

import httpx
import pytest
from composer_crawler import CrawlConfig, Crawler

Handler = Callable[[httpx.Request], httpx.Response]

SITE = {
    "/": '<a href="/a">a</a> <a href="/b">b</a> <a href="https://off.example/x">x</a> <a href="/a">dup</a>',
    "/a": '<a href="/deep">deep</a>',
    "/deep": "leaf",
}


def _site_handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == "/robots.txt":
        return httpx.Response(200, text="User-agent: *\nDisallow: /a\n")
    if path == "/b":
        return httpx.Response(404, text="gone", headers={"Content-Type": "text/html"})
    if path in SITE:
        return httpx.Response(200, text=SITE[path], headers={"Content-Type": "text/html"})
    return httpx.Response(404, text="missing", headers={"Content-Type": "text/html"})


def _config(
    *,
    follow_links: bool = True,
    allow_patterns: tuple[str, ...] = (r"https://example\.org/",),
    max_depth: int = 2,
    respect_robots: bool = False,
) -> CrawlConfig:
    return CrawlConfig(
        name="site",
        seeds=("https://example.org/",),
        follow_links=follow_links,
        allow_patterns=allow_patterns,
        max_depth=max_depth,
        request_delay_s=0.0,
        respect_robots=respect_robots,
    )


def _crawler(config: CrawlConfig, handler: Handler = _site_handler) -> Crawler:
    return Crawler(config, client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_bfs_crawl_dedupes_and_filters_links() -> None:
    records = list(_crawler(_config()).crawl())
    assert [(r.url, r.depth) for r in records] == [
        ("https://example.org/", 0),
        ("https://example.org/a", 1),
        ("https://example.org/b", 1),
        ("https://example.org/deep", 2),
    ]
    # the off-pattern host was never fetched; the duplicate /a link was fetched once


def test_max_pages_caps_total_requests() -> None:
    records = list(_crawler(_config()).crawl(max_pages=2))
    assert [r.url for r in records] == ["https://example.org/", "https://example.org/a"]


def test_max_depth_stops_link_following() -> None:
    records = list(_crawler(_config(max_depth=1)).crawl())
    assert [r.url for r in records] == [
        "https://example.org/",
        "https://example.org/a",
        "https://example.org/b",
    ]


def test_robots_disallow_skips_url() -> None:
    records = list(_crawler(_config(respect_robots=True)).crawl())
    urls = [r.url for r in records]
    assert "https://example.org/a" not in urls
    assert "https://example.org/" in urls


def test_error_page_is_recorded_but_not_followed() -> None:
    records = list(_crawler(_config()).crawl())
    b = next(r for r in records if r.url.endswith("/b"))
    assert b.status_code == 404
    assert b.body == "gone"


def test_seed_failure_propagates_after_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    import composer_crawler._http

    monkeypatch.setattr(composer_crawler._http.time, "sleep", lambda _s: None)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    with pytest.raises(httpx.HTTPStatusError):
        list(_crawler(_config(), handler).crawl())


def test_failed_discovered_link_is_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    import composer_crawler._http

    monkeypatch.setattr(composer_crawler._http.time, "sleep", lambda _s: None)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/a":
            return httpx.Response(500, text="boom")
        return _site_handler(request)

    records = list(_crawler(_config(), handler).crawl())
    urls = [r.url for r in records]
    assert "https://example.org/a" not in urls
    assert "https://example.org/b" in urls  # crawl continued past the failure


def test_non_text_body_is_skipped() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/a":
            return httpx.Response(200, content=b"\x89PNG", headers={"Content-Type": "image/png"})
        return _site_handler(request)

    records = list(_crawler(_config(), handler).crawl())
    assert "https://example.org/a" not in [r.url for r in records]


def test_non_html_content_skips_link_extraction() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, text='{"href": "/a"} <a href="/a">a</a>', headers={"Content-Type": "application/json"}
        )

    records = list(_crawler(_config(), handler).crawl())
    assert [r.url for r in records] == ["https://example.org/"]


def test_record_metadata_captured() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text="ok",
            headers={"Content-Type": "text/html; charset=utf-8", "ETag": '"abc"'},
        )

    record = next(iter(_crawler(_config(follow_links=False, allow_patterns=()), handler).crawl()))
    assert record.content_type == "text/html"
    assert record.headers["etag"] == '"abc"'
    assert record.final_url == "https://example.org/"
    assert record.fetched_at  # ISO timestamp present
