class NotFoundError(Exception):
    """A requested resource does not exist; the app maps it to HTTP 404."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail
