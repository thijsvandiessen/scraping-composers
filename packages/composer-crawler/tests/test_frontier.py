from composer_crawler.frontier import Frontier, extract_links


def test_extract_links_resolves_relative_urls() -> None:
    html = '<a href="/works">works</a> <a href="detail.html">detail</a>'
    assert extract_links(html, "https://example.org/composers/") == [
        "https://example.org/works",
        "https://example.org/composers/detail.html",
    ]


def test_extract_links_strips_fragments_and_skips_non_http() -> None:
    html = (
        '<a href="https://example.org/a#section">a</a>'
        '<a href="mailto:x@example.org">mail</a>'
        '<a href="javascript:void(0)">js</a>'
    )
    assert extract_links(html, "https://example.org/") == ["https://example.org/a"]


def test_extract_links_handles_single_and_double_quotes() -> None:
    html = "<a href='/single'>s</a> <a href=\"/double\">d</a>"
    assert extract_links(html, "https://example.org/") == [
        "https://example.org/single",
        "https://example.org/double",
    ]


def test_frontier_dedupes_and_preserves_fifo_order() -> None:
    frontier = Frontier()
    assert frontier.add("https://example.org/a", 0)
    assert frontier.add("https://example.org/b", 1)
    assert not frontier.add("https://example.org/a", 2)  # re-add is a no-op
    assert not frontier.add("https://example.org/a#frag", 2)  # same after normalization
    assert frontier.pop() == ("https://example.org/a", 0)
    assert frontier.pop() == ("https://example.org/b", 1)
    assert frontier.pop() is None
    assert not frontier
