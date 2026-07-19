import httpx
from composer_crawler.robots import RobotsCache

ROBOTS = """
User-agent: *
Disallow: /private/
"""


def _cache(handler: httpx.MockTransport) -> RobotsCache:
    return RobotsCache(httpx.Client(transport=handler))


def test_disallowed_path_is_blocked_others_allowed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/robots.txt"
        return httpx.Response(200, text=ROBOTS)

    cache = _cache(httpx.MockTransport(handler))
    assert not cache.allowed("https://example.org/private/page")
    assert cache.allowed("https://example.org/public/page")


def test_missing_robots_allows_all() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    cache = _cache(httpx.MockTransport(handler))
    assert cache.allowed("https://example.org/anything")


def test_unreachable_robots_allows_all() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    cache = _cache(httpx.MockTransport(handler))
    assert cache.allowed("https://example.org/anything")


def test_robots_fetched_once_per_host() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, text=ROBOTS)

    cache = _cache(httpx.MockTransport(handler))
    cache.allowed("https://example.org/a")
    cache.allowed("https://example.org/b")
    cache.allowed("https://other.example/c")
    assert calls == ["https://example.org/robots.txt", "https://other.example/robots.txt"]
