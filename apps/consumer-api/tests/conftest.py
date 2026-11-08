"""Pull in the shared warehouse fixtures via a plain import: ``pytest_plugins``
is only legal in the rootdir conftest, which this is not when pytest runs from
the workspace root."""

from composer_warehouse.testing import session as session
