"""Pull in the shared warehouse fixtures via a plain import: ``pytest_plugins``
is only legal in the rootdir conftest, which this is not when pytest runs from
the workspace root."""

import pytest
from composer_warehouse.testing import session as session


# tryfirst: xdist reads the group in its own collection hook, so the marker
# has to be on the item before that runs.
@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Keep the tests that load Neo4j on one xdist worker.

    The ``graph`` fixture empties the server it is pointed at, so two of these
    tests on different workers would wipe each other's graph. CI runs with
    ``--dist loadgroup``, which sends every test in a group to the same worker.
    """
    for item in items:
        if "graph" in getattr(item, "fixturenames", ()):
            item.add_marker(pytest.mark.xdist_group("neo4j"))
