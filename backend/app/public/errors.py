"""Machine-readable errors from the public extension contracts."""


class PublicError(Exception):
    """An error that an extension can handle without parsing prose."""

    def __init__(
        self,
        code: str,
        detail: str,
        *,
        status_code: int = 400,
        retryable: bool = False,
        obligations: tuple[str, ...] = (),
    ) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.status_code = status_code
        self.retryable = retryable
        self.obligations = tuple(obligations)
