from __future__ import annotations


class HTTPException(Exception):
    """A request-shape problem the transport layer itself must reject (bad JSON, an unsupported
    content type, a missing query, a disallowed method, ...) -- distinct from a `GraphQLError`,
    which describes a problem with the *GraphQL operation itself* once execution is already
    underway. Each concrete adapter (ASGI, ...) is responsible for turning this into whatever its
    own framework's error response looks like.
    """

    def __init__(self, status_code: int, message: str) -> None:
        self.status_code = status_code
        self.message = message
        super().__init__(message)
