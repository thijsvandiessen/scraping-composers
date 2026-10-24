class NotFoundError(Exception):
    """A requested resource does not exist; the app maps it to HTTP 404."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class GraphUnavailableError(Exception):
    """The graph database is not configured or not reachable; the app maps it to HTTP 503."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail
