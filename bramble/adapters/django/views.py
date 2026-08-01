"""An async Django view serving GraphQL over HTTP only. There's deliberately no sync Django view
variant here: Django's classic synchronous request path can't do WebSocket either way (see
`bramble.adapters.django.channels` for that), so keeping the HTTP side async-only avoids a second,
largely redundant `SyncBaseHTTPView` stack for a single adapter. Django detects and runs an
`async def` view function itself (via its own WSGI/ASGI handler's `async_to_sync`/native async
dispatch) -- no extra wiring needed beyond defining the view as a plain coroutine function.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Coroutine, Mapping
from typing import TYPE_CHECKING, Any

from django.http import HttpRequest, HttpResponse, JsonResponse, StreamingHttpResponse

from bramble.http.async_base_view import AsyncBaseHTTPView
from bramble.http.exceptions import HTTPException
from bramble.http.multipart import multipart_content_type

if TYPE_CHECKING:
    from django.core.files.uploadedfile import UploadedFile

    from bramble._schema import Schema


class _DjangoRequestAdapter:
    """Satisfies `bramble.http.base.BaseRequestProtocol` structurally, over Django's own
    `HttpRequest`.
    """

    def __init__(self, request: HttpRequest) -> None:
        self._request = request

    @property
    def method(self) -> str:
        return self._request.method or "GET"

    @property
    def query_params(self) -> Mapping[str, str]:
        return self._request.GET.dict()

    @property
    def headers(self) -> Mapping[str, str]:
        return self._request.headers


class _DjangoUploadFile:
    """The value a resolver sees for an `Upload!` argument -- wraps Django's `UploadedFile`
    (whose own `read()` is synchronous) with an `async def read()` so resolver code stays
    portable across adapters.
    """

    def __init__(self, uploaded_file: "UploadedFile") -> None:
        self.filename = uploaded_file.name
        self._uploaded_file = uploaded_file

    async def read(self) -> bytes:
        return self._uploaded_file.read()


class _GraphQLHTTPHandler(AsyncBaseHTTPView[HttpRequest, HttpResponse, Any, Any]):
    def __init__(self, schema: "Schema", *, multipart_uploads_enabled: bool = True) -> None:
        self.schema = schema
        self.multipart_uploads_enabled = multipart_uploads_enabled

    async def get_body(self, request: HttpRequest) -> bytes:
        return request.body

    async def get_form_data(self, request: HttpRequest) -> Mapping[str, Any]:
        fields: dict[str, Any] = dict(request.POST.dict())
        for name, uploaded_file in request.FILES.items():
            fields[name] = _DjangoUploadFile(uploaded_file)
        return fields

    async def get_context(self, request: HttpRequest) -> Any:
        return {"request": request}

    def create_response(self, response_data: Any) -> HttpResponse:
        return JsonResponse(response_data, safe=not isinstance(response_data, list))

    def create_html_response(self, html: str) -> HttpResponse:
        return HttpResponse(html, content_type="text/html; charset=utf-8")

    async def create_multipart_response(self, stream: AsyncIterator[bytes]) -> HttpResponse:
        return StreamingHttpResponse(stream, content_type=multipart_content_type())

    def is_websocket_request(self, request: Any) -> Any:
        return False

    async def dispatch(self, request: HttpRequest) -> HttpResponse:
        protocol_request = _DjangoRequestAdapter(request)
        try:
            return await self.run(request, protocol_request)
        except HTTPException as error:
            return JsonResponse(
                {"errors": [{"message": error.message}]}, status=error.status_code
            )


def graphql_view(
    schema: "Schema", *, multipart_uploads_enabled: bool = True
) -> Callable[..., Coroutine[Any, Any, HttpResponse]]:
    """An async Django view callable serving `schema`, ready for `urlpatterns`, e.g.
    `path("graphql", graphql_view(schema))`.
    """
    handler = _GraphQLHTTPHandler(schema, multipart_uploads_enabled=multipart_uploads_enabled)

    async def view(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        return await handler.dispatch(request)

    return view


__all__ = ["graphql_view"]
