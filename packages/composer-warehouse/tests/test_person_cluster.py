"""Tests for the clustering step that turns scored pairs into duplicate groups."""

import uuid

from composer_warehouse.persons import Edge, build_clusters


def _ids(n: int) -> list[uuid.UUID]:
    """``n`` ids in ascending order, so tie-breaks in the tests are readable."""
    return sorted(uuid.uuid4() for _ in range(n))


def test_a_chain_of_pairs_becomes_one_cluster() -> None:
    a, b, c = _ids(3)
    clustering = build_clusters([Edge(a, b, 0.99), Edge(b, c, 0.99)])

    assert clustering.clusters == (frozenset({a, b, c}),)
    assert clustering.refused == ()


def test_unrelated_pairs_stay_separate() -> None:
    a, b, c, d = _ids(4)
    clustering = build_clusters([Edge(a, b, 0.99), Edge(c, d, 0.99)])

    assert sorted(clustering.clusters, key=lambda g: sorted(g)) == [{a, b}, {c, d}]
    assert (clustering.members, clustering.largest) == (4, 2)


def test_a_record_with_no_surviving_edge_is_in_no_cluster() -> None:
    a, b, c = _ids(3)
    clustering = build_clusters([Edge(a, b, 0.99)], cannot_link=[(a, c)])

    assert clustering.clusters == (frozenset({a, b}),)


def test_a_cannot_link_refuses_the_pair() -> None:
    a, b = _ids(2)
    clustering = build_clusters([Edge(a, b, 0.99)], cannot_link=[(a, b)])

    assert clustering.clusters == ()
    assert clustering.refused == (Edge(a, b, 0.99),)


def test_a_cannot_link_refuses_a_transitive_merge() -> None:
    """The whole point of clustering the pairs rather than deciding them.

    ``A~C`` and ``C~B`` would merge A with B, and a pairwise pass has no way to
    notice: it only ever declined the pair ``A~B``, which nothing re-proposes.
    """
    a, b, c = _ids(3)
    clustering = build_clusters(
        [Edge(a, c, 0.99), Edge(c, b, 0.95)],
        cannot_link=[(a, b)],
    )

    assert clustering.clusters == (frozenset({a, c}),)
    assert clustering.refused == (Edge(c, b, 0.95),)  # the weaker edge is the one dropped


def test_the_strongest_edge_survives_a_refusal() -> None:
    """Merges are applied strongest first, so a constraint drops the weakest
    evidence rather than whichever edge the iteration happened to reach first."""
    a, b, c = _ids(3)
    edges = [Edge(c, b, 0.95), Edge(a, c, 0.99)]

    for order in (edges, list(reversed(edges))):
        clustering = build_clusters(order, cannot_link=[(a, b)])
        assert clustering.clusters == (frozenset({a, c}),)


def test_the_partition_does_not_depend_on_edge_order() -> None:
    a, b, c, d = _ids(4)
    edges = [Edge(a, b, 0.99), Edge(c, d, 0.98), Edge(b, c, 0.97)]

    first = build_clusters(edges)
    second = build_clusters(reversed(edges))

    assert first.clusters == second.clusters == (frozenset({a, b, c, d}),)


def test_a_duplicated_edge_is_absorbed() -> None:
    a, b = _ids(2)
    clustering = build_clusters([Edge(a, b, 0.99), Edge(b, a, 0.99)])

    assert clustering.clusters == (frozenset({a, b}),)
    assert clustering.refused == ()
