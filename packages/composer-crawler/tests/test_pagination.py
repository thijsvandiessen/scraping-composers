import httpx
from composer_crawler import CrawlConfig, Crawler, NextUrlFromJson, PageParam


def _crawl(config: CrawlConfig, handler: httpx.MockTransport) -> list[str]:
    crawler = Crawler(config, client=httpx.Client(transport=handler))
    return [record.url for record in crawler.crawl()]


def _config(pagination: NextUrlFromJson | PageParam, seed: str) -> CrawlConfig:
    return CrawlConfig(
        name="api",
        seeds=(seed,),
        pagination=pagination,
        request_delay_s=0.0,
        respect_robots=False,
    )


def test_next_url_from_json_follows_dot_path() -> None:
    pages = {
        "/items?page=1": {"items": [1], "meta": {"next": "/items?page=2"}},
        "/items?page=2": {"items": [2], "meta": {"next": None}},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=pages[request.url.raw_path.decode()])

    urls = _crawl(
        _config(NextUrlFromJson("meta.next"), "https://api.example/items?page=1"),
        httpx.MockTransport(handler),
    )
    assert urls == ["https://api.example/items?page=1", "https://api.example/items?page=2"]


def test_next_url_from_json_stops_on_missing_pointer() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"items": [1]})

    urls = _crawl(
        _config(NextUrlFromJson("meta.next"), "https://api.example/items"),
        httpx.MockTransport(handler),
    )
    assert urls == ["https://api.example/items"]


def test_page_param_increments_until_empty_body() -> None:
    bodies = {"1": "alpha", "2": "beta", "3": ""}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=bodies[request.url.params["page"]])

    urls = _crawl(
        _config(PageParam(), "https://example.org/list?page=1"),
        httpx.MockTransport(handler),
    )
    assert urls == [
        "https://example.org/list?page=1",
        "https://example.org/list?page=2",
        "https://example.org/list?page=3",
    ]


def test_page_param_stops_on_repeated_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="same every time")

    urls = _crawl(
        _config(PageParam(), "https://example.org/list?page=1"),
        httpx.MockTransport(handler),
    )
    assert urls == ["https://example.org/list?page=1", "https://example.org/list?page=2"]


def test_page_param_stops_on_empty_json_array() -> None:
    bodies = {"1": '[{"id": 1}]', "2": "[]"}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, text=bodies[request.url.params["page"]], headers={"Content-Type": "application/json"}
        )

    urls = _crawl(
        _config(PageParam(), "https://api.example/items?page=1"),
        httpx.MockTransport(handler),
    )
    assert urls == ["https://api.example/items?page=1", "https://api.example/items?page=2"]


def test_page_param_adds_param_from_start_when_absent() -> None:
    bodies = {None: "first", "2": ""}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=bodies[request.url.params.get("page")])

    urls = _crawl(
        _config(PageParam(start=1), "https://example.org/list"),
        httpx.MockTransport(handler),
    )
    assert urls == ["https://example.org/list", "https://example.org/list?page=2"]
