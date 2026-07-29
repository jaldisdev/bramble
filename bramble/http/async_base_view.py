from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Generic, TypeVar

from bramble._error import GraphQLError, error_to_dict
from bramble.http.base import BaseRequestProtocol, BaseView
from bramble.http.exceptions import HTTPException
from bramble.http.parse_content_type import parse_content_type
from bramble.http.types import GraphQLRequestData

if TYPE_CHECKING:
    from bramble._schema import Schema

Request = TypeVar("Request")
Response = TypeVar("Response")


class AsyncBaseHTTPView(BaseView[Request], Generic[Request, Response]):
    """The async request/response cycle every concrete adapter (ASGI now; a WSGI-based one later,
    per the roadmap) drives: parse whatever shape the request came in as (GET query params, a
    single JSON body, a batched JSON array, or a multipart file-upload request) into one or more
    `GraphQLRequestData`, execute each against `self.schema`, and hand the result(s) back through
    `create_response`/`create_html_response` for the concrete adapter to turn into its own
    framework's actual response type.

    Every method below that isn't already implemented is exactly what a concrete adapter must
    supply -- reading the raw body, parsing multipart form fields, building the resolver context/
    root value, and constructing a real response object are all inherently framework-specific.
    """

    schema: "Schema"

    async def get_body(self, request: Request) -> bytes:
        raise NotImplementedError

    async def get_form_data(self, request: Request) -> Mapping[str, Any]:
        raise NotImplementedError

    async def get_context(self, request: Request) -> Any:
        raise NotImplementedError

    async def get_root_value(self, request: Request) -> Any:
        return None

    def create_response(self, response_data: dict[str, Any] | list[dict[str, Any]]) -> Response:
        raise NotImplementedError

    def create_html_response(self, html: str) -> Response:
        raise NotImplementedError

    def _max_batch_operations(self) -> int | None:
        batching_config = self.schema.config.batching_config
        return batching_config["max_operations"] if batching_config is not None else None

    async def _parse_post_body(
        self, request: Request, protocol_request: BaseRequestProtocol
    ) -> list[GraphQLRequestData]:
        content_type, _ = parse_content_type(protocol_request.headers.get("content-type", ""))

        if content_type == "multipart/form-data":
            if not self.multipart_uploads_enabled:
                raise HTTPException(400, "File uploads are not enabled for this schema")
            form = await self.get_form_data(request)
            return self.parse_multipart_operations(form)

        body = await self.get_body(request)
        if not body:
            raise HTTPException(400, "No GraphQL query found in the request")
        parsed = self.parse_json(body)

        if isinstance(parsed, list):
            return self.parse_batch(parsed, max_operations=self._max_batch_operations())
        return [self.request_data_from_dict(parsed)]

    async def _execute(self, request_data: GraphQLRequestData, context: Any, root_value: Any) -> dict[str, Any]:
        if request_data.query is None:
            raise HTTPException(400, "No GraphQL query found in the request")
        try:
            return await self.schema.execute_async(
                request_data.query,
                variable_values=request_data.variables,
                context=context,
                root_value=root_value,
                operation_name=request_data.operation_name,
            )
        except GraphQLError as error:
            # A request-level failure (malformed query, unknown operation, ...) raised directly
            # by `execute_async` rather than returned inside a successful response's own
            # `errors` list -- still rendered in the same spec shape, just as the whole response.
            return {"data": None, "errors": [error_to_dict(error)]}

    async def run(self, request: Request, protocol_request: BaseRequestProtocol) -> Response:
        if not self.is_request_allowed(protocol_request):
            raise HTTPException(405, "GraphQL only supports GET and POST requests")

        if self.should_render_graphql_ide(protocol_request):
            return self.create_html_response(self.graphql_ide_html)

        if protocol_request.method == "GET":
            request_data_list = [self.request_data_from_dict(self.parse_query_params(protocol_request.query_params))]
        else:
            request_data_list = await self._parse_post_body(request, protocol_request)

        context = await self.get_context(request)
        root_value = await self.get_root_value(request)

        if len(request_data_list) > 1:
            results = await asyncio.gather(
                *(self._execute(request_data, context, root_value) for request_data in request_data_list)
            )
            return self.create_response(list(results))

        result = await self._execute(request_data_list[0], context, root_value)
        return self.create_response(result)
