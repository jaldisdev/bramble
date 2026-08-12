#
# This source file is part of the Bramble open source project.
#
# Copyright (c) 2026 Jaldis B.V.
#
# Licensed under the MIT OR Apache-2.0 license (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://opensource.org/licenses/MIT
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

"""An async Django view serving GraphQL over HTTP only. There's deliberately no sync Django view
variant here: Django's classic synchronous request path can't do WebSocket either way (see
`bramble.adapters.django.channels` for that), so keeping the HTTP side async-only avoids a second,
largely redundant `SyncBaseHTTPView` stack for a single adapter. Django detects and runs an
`async def` view function itself (via its own WSGI/ASGI handler's `async_to_sync`/native async
dispatch) -- no extra wiring needed beyond defining the view as a plain coroutine function.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable, Coroutine, Mapping
from typing import TYPE_CHECKING, Any

from asgiref.sync import markcoroutinefunction
from django.core.serializers.json import DjangoJSONEncoder
from django.http import HttpRequest, HttpResponse, JsonResponse, StreamingHttpResponse

from bramble.http.async_base_view import AsyncBaseHTTPView
from bramble.http.exceptions import HTTPException
from bramble.http.multipart import multipart_content_type
from bramble.http.types import TemporalResponse

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


class AsyncGraphQLView(AsyncBaseHTTPView[HttpRequest, HttpResponse, Any, Any]):
    """An async Django view serving `schema` over HTTP. Mount it with `as_view`:

        path("graphql", AsyncGraphQLView.as_view(schema=schema))

    Subclass it to supply your own resolver context, response, or error handling:

        class MyGraphQLView(AsyncGraphQLView):
            async def get_context(self, request: HttpRequest) -> Any:
                return MyContext(request=request, response=self.sub_response, user=request.user)

    A new instance is constructed per request (`as_view` does that, the same way Django's own
    class-based views do), so anything a hook stores on `self` -- `self.sub_response` in particular
    -- is request-scoped and safe to mutate.

    Every hook `bramble.http.AsyncBaseHTTPView` defines is overridable here; the ones a Django app
    reaches for most are `get_context`, `get_root_value`, `create_response`, and `dispatch`.
    """

    def __init__(
        self,
        schema: "Schema",
        *,
        multipart_uploads_enabled: bool = True,
        graphql_ide: bool = True,
        json_encoder: type[json.JSONEncoder] = DjangoJSONEncoder,
    ) -> None:
        """Arguments:
        schema: the `bramble.Schema` this view serves.
        multipart_uploads_enabled: whether `multipart/form-data` file-upload requests are
            accepted. `False` rejects them with a 400.
        graphql_ide: whether a browser `GET` with no `query` renders the GraphiQL page. Pass
            `False` for a production endpoint that shouldn't serve an interactive console.
        json_encoder: the `json.JSONEncoder` subclass the response body is serialised with.
            Defaults to Django's own, which already handles dates/decimals/UUIDs.
        """
        self.schema = schema
        self.multipart_uploads_enabled = multipart_uploads_enabled
        self.graphql_ide = graphql_ide
        self.json_encoder = json_encoder
        # Available to `get_context` (and through it to resolvers/extensions) for the whole
        # request, then copied onto the real response by `dispatch` -- see `TemporalResponse`.
        self.sub_response = TemporalResponse()

    @classmethod
    def as_view(cls, **initkwargs: Any) -> Callable[..., Coroutine[Any, Any, HttpResponse]]:
        """The async view callable to put in `urlpatterns`, constructing a fresh view instance per
        request. `initkwargs` are this class's own constructor arguments (`schema=...` at minimum).

        Named and shaped after Django's `View.as_view()` -- including that decorators apply to what
        it returns (`csrf_exempt(AsyncGraphQLView.as_view(schema=schema))`) -- but this is not a
        Django `View` subclass: the GraphQL request cycle is one POST handler, not a method-dispatch
        table, and inheriting Django's sync-oriented dispatch would only add a layer to override.
        """

        async def view(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
            return await cls(**initkwargs).dispatch(request, *args, **kwargs)

        # Django decides whether to await a view or run it in a threadpool by asking
        # `iscoroutinefunction()`. `view` above already is one, but marking it explicitly is what
        # keeps that true for anything that wraps it (`method_decorator`, a sync-looking decorator
        # stack) -- the same thing Django's own `View.as_view` does for an async view.
        markcoroutinefunction(view)
        return view

    async def get_body(self, request: HttpRequest) -> bytes:
        return request.body

    async def get_form_data(self, request: HttpRequest) -> Mapping[str, Any]:
        fields: dict[str, Any] = dict(request.POST.dict())
        for name, uploaded_file in request.FILES.items():
            fields[name] = _DjangoUploadFile(uploaded_file)
        return fields

    async def get_context(self, request: HttpRequest) -> Any:
        """The value resolvers see as `info.context`. Override to return your own object -- a
        dataclass holding the authenticated user, a database client, and `self.sub_response`, say.
        """
        return {"request": request, "response": self.sub_response}

    def create_response(self, response_data: Any) -> HttpResponse:
        return JsonResponse(
            response_data,
            safe=not isinstance(response_data, list),
            encoder=self.json_encoder,
        )

    def create_html_response(self, html: str) -> HttpResponse:
        return HttpResponse(html, content_type="text/html; charset=utf-8")

    async def create_multipart_response(self, stream: AsyncIterator[bytes]) -> HttpResponse:
        return StreamingHttpResponse(stream, content_type=multipart_content_type())

    def is_websocket_request(self, request: Any) -> Any:
        return False

    def apply_sub_response(self, response: HttpResponse) -> HttpResponse:
        """Copies whatever execution set on `self.sub_response` onto the real response.

        A status code explicitly set during the request wins over the one the response was built
        with; headers are merged in rather than replacing anything already there. Deliberately does
        *not* touch an error response built by `dispatch`'s own `HTTPException` handler -- that
        request never reached execution, so there is nothing for a resolver to have overridden.
        """
        if self.sub_response.status_code != 200:
            response.status_code = self.sub_response.status_code
        for name, value in self.sub_response.headers.items():
            response[name] = value
        return response

    async def dispatch(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        protocol_request = _DjangoRequestAdapter(request)
        try:
            response = await self.run(request, protocol_request)
        except HTTPException as error:
            return JsonResponse(
                {"errors": [{"message": error.message}]}, status=error.status_code
            )
        return self.apply_sub_response(response)


def graphql_view(
    schema: "Schema",
    *,
    multipart_uploads_enabled: bool = True,
    graphql_ide: bool = True,
    json_encoder: type[json.JSONEncoder] = DjangoJSONEncoder,
    view_class: type[AsyncGraphQLView] = AsyncGraphQLView,
) -> Callable[..., Coroutine[Any, Any, HttpResponse]]:
    """An async Django view callable serving `schema`, ready for `urlpatterns`, e.g.
    `path("graphql", graphql_view(schema))`.

    Equivalent to `AsyncGraphQLView.as_view(schema=schema, ...)`; use the class directly (or pass a
    subclass as `view_class`) when you need to override a hook. See `AsyncGraphQLView.__init__` for
    the shared arguments.
    """
    return view_class.as_view(
        schema=schema,
        multipart_uploads_enabled=multipart_uploads_enabled,
        graphql_ide=graphql_ide,
        json_encoder=json_encoder,
    )


__all__ = ["AsyncGraphQLView", "graphql_view"]
