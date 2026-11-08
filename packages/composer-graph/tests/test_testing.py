"""The lock that keeps Neo4j-backed tests from wiping each other's graph."""

import threading

from composer_graph.testing import exclusive_neo4j


def test_a_second_holder_waits_until_the_first_releases() -> None:
    url = "bolt://lock-test.invalid:7687"
    entered = threading.Event()

    def second() -> None:
        with exclusive_neo4j(url):
            entered.set()

    with exclusive_neo4j(url):
        thread = threading.Thread(target=second)
        thread.start()
        assert not entered.wait(0.3)
    assert entered.wait(5)
    thread.join()


def test_different_servers_do_not_block_each_other() -> None:
    with exclusive_neo4j("bolt://a.invalid:7687"), exclusive_neo4j("bolt://b.invalid:7687"):
        pass
