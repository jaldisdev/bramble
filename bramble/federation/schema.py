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

"""`bramble.federation.Schema` -- a real subclass of `bramble.Schema` (not a delegating wrapper),
serving `query` as an Apollo Federation v2 subgraph: it adds `_service { sdl }` and
`_entities(representations: [_Any!]!): [_Entity]!` to `query`, builds the `_Entity` union over
every type in `types` that carries an `@key` directive, and applies `@link` declaring this
subgraph's own federation spec version. See `bramble/federation/directives.py` for the directive
set and `bramble/federation/scalars.py` for `_Any`/`FieldSet`.
"""

from __future__ import annotations

import dataclasses
import inspect
from collections.abc import Sequence
from typing import Annotated, Any, Union

from bramble._bramble import SchemaError
from bramble._execution import _effective_name
from bramble._resolver import Info
from bramble._schema import Schema as _BaseSchema
from bramble._type import field as _field
from bramble._type import type as _type_decorator
from bramble._union import union as _union
from bramble.federation.directives import Key, Link
from bramble.federation.scalars import AnyDefinition, FieldSet, FieldSetDefinition, _Any
from bramble.schema.config import SchemaConfig

_type = type  # capture the builtin before it's shadowed by `_type_decorator`'s own name below

# Every federation directive's own GraphQL-facing (camelCase) name -- used only to build `@link`'s
# `import` list from whichever of these actually got applied somewhere in this schema, not for
# validation (each directive's own location/shape is already enforced by `@bramble.schema_directive`
# itself, the same way any other directive is).
_FEDERATION_DIRECTIVE_NAMES = frozenset(
    {
        "key",
        "shareable",
        "external",
        "requires",
        "provides",
        "override",
        "inaccessible",
        "tag",
        "interfaceObject",
        "composeDirective",
        "authenticated",
        "requiresScopes",
        "policy",
    }
)


@_type_decorator
class _Service:
    sdl: str


def _scan_used_federation_directive_names(roots: Sequence[_type]) -> set[str]:
    """A best-effort pre-scan (type-level and field-level applied directives only, not argument-
    level) over `roots` for which federation directives are actually in use -- purely to populate
    `@link(import: [...])`'s informational list before `Schema.__init__` proper's own graph walk
    (which discovers the *full* type graph, but only runs inside `super().__init__()`, too late for
    this constructor argument) ever runs.
    """
    used: set[str] = set()
    for cls in roots:
        for directive in getattr(cls, "__bramble_applied_directives__", ()):
            info = getattr(_type(directive), "__bramble_directive_info__", None)
            if info is not None and info.name in _FEDERATION_DIRECTIVE_NAMES:
                used.add(info.name)
        if dataclasses.is_dataclass(cls):
            for dataclass_field in dataclasses.fields(cls):
                for directive in getattr(dataclass_field, "directives", ()):
                    info = getattr(_type(directive), "__bramble_directive_info__", None)
                    if info is not None and info.name in _FEDERATION_DIRECTIVE_NAMES:
                        used.add(info.name)
    return used


def _validate_key_fields(cls: _type, keys: Sequence[Key], *, auto_camel_case: bool) -> None:
    type_info = getattr(cls, "__bramble_type_info__", None)
    if type_info is None:
        raise SchemaError(
            f"'{cls}' carries an @key directive but is not a @bramble.type-decorated class"
        )
    field_names = {
        _effective_name(field_info.name, field_info.graphql_name, auto_camel_case=auto_camel_case)
        for field_info in type_info.fields
    }
    for key in keys:
        if "{" in key.fields or "}" in key.fields:
            raise SchemaError(
                f'@key(fields="{key.fields}") on \'{cls.__name__}\': nested/braced selection sets '
                'are not yet supported -- only flat, space-separated field lists (e.g. "id", "id sku")'
            )
        for field_name in key.fields.split():
            if field_name not in field_names:
                raise SchemaError(
                    f'@key(fields="{key.fields}") on \'{cls.__name__}\' references unknown field '
                    f"'{field_name}'"
                )


def _discover_entity_types(types: Sequence[_type], *, auto_camel_case: bool) -> list[_type]:
    entity_types: list[_type] = []
    for cls in types:
        keys = [d for d in getattr(cls, "__bramble_applied_directives__", ()) if isinstance(d, Key)]
        if not keys:
            continue
        _validate_key_fields(cls, keys, auto_camel_case=auto_camel_case)
        entity_types.append(cls)
    return entity_types


def _build_entity_union(entity_types: Sequence[_type]) -> Any:
    # A `Union[X]` of exactly one member collapses to `X` itself in Python (no real `Union` object
    # to introspect) -- wrapping a single class directly in `Annotated[X, union(...)]` still
    # registers a valid one-member GraphQL union (`describe_union` doesn't require its own
    # `underlying` to literally be a `typing.Union`), so this only reaches for `Union[...]` when
    # there are 2+ members to actually union together.
    member_type = entity_types[0] if len(entity_types) == 1 else Union[tuple(entity_types)]
    return Annotated[member_type, _union("_Entity")]


async def _resolve_service(info: Info) -> _Service:
    return _Service(sdl=info.schema.to_sdl())


async def _resolve_entities(representations: list[_Any], info: Info) -> list[Any]:
    results: list[Any] = []
    for representation in representations:
        typename = representation.get("__typename")
        entity_type = info.schema.types_by_name.get(typename) if typename else None
        resolve_reference = getattr(entity_type, "resolve_reference", None) if entity_type is not None else None
        if entity_type is None or resolve_reference is None:
            results.append(None)
            continue

        graphql_to_python = {
            _effective_name(
                field_info.name, field_info.graphql_name, auto_camel_case=info.schema.config.auto_camel_case
            ): field_info.name
            for field_info in entity_type.__bramble_type_info__.fields
        }
        kwargs = {
            graphql_to_python.get(key, key): value for key, value in representation.items() if key != "__typename"
        }
        # A `resolve_reference` that declares its own `info` parameter (by name -- this is a fixed,
        # well-known extension point, not a generically-shaped resolver, so name-based detection is
        # enough) gets the same `Info` this field's own resolution is running with, e.g. for
        # `info.context` database access; one that doesn't is called with just the key fields.
        if "info" in inspect.signature(resolve_reference).parameters:
            kwargs["info"] = info

        result = resolve_reference(**kwargs)
        if inspect.isawaitable(result):
            result = await result
        results.append(result)
    return results


def _build_federated_query(query: _type, entity_types: Sequence[_type]) -> _type:
    # Explicit `name=` overrides on both fields: auto_camel_case's leading-underscore handling
    # would otherwise mangle `_service`/`_entities` into `Service`/`Entities` (the underscore
    # itself is treated as a word separator, capitalizing the next letter) -- these two names are
    # spec-fixed, never subject to bramble's own camelCase convention.
    annotations: dict[str, Any] = {"_service": _Service}
    namespace: dict[str, Any] = {"_service": _field(resolver=_resolve_service, name="_service")}

    if entity_types:
        entity_annotation = _build_entity_union(entity_types)
        annotations["_entities"] = list[entity_annotation | None]  # type: ignore[valid-type]
        namespace["_entities"] = _field(resolver=_resolve_entities, name="_entities")

    namespace["__annotations__"] = annotations
    federated_query = _type(f"_Federated{query.__name__}", (query,), namespace)
    # `description=`/`directives=` carried over for the same reason `bramble._schema`'s own
    # introspection subclass does: a re-decorated subclass takes its metadata from the decorator's
    # arguments, so omitting these drops the user's query-type description/directives from SDL.
    return _type_decorator(
        federated_query,
        name=query.__bramble_type_info__.name,
        description=query.__bramble_type_info__.description,
        directives=getattr(query, "__bramble_applied_directives__", ()),
    )


class Schema(_BaseSchema):
    def __init__(
        self,
        query: _type,
        mutation: _type | None = None,
        subscription: _type | None = None,
        types: Sequence[_type] = (),
        federation_version: str = "2.6",
        config: SchemaConfig | None = None,
        **kwargs: Any,
    ) -> None:
        if getattr(query, "__bramble_type_info__", None) is None:
            raise SchemaError("federation.Schema(query=...) must be a @bramble.type-decorated class")

        effective_config = config if config is not None else SchemaConfig()
        entity_types = _discover_entity_types(types, auto_camel_case=effective_config.auto_camel_case)
        # `_Service` and the `_Entity` union are never passed via `types=` -- like any other
        # object/union only ever referenced through a field's own return type, the base
        # `Schema.__init__`'s graph walk discovers them automatically by following
        # `_service`/`_entities`' annotations on `synthetic_query` below.
        synthetic_query = _build_federated_query(query, entity_types)

        scalar_map = dict(effective_config.scalar_map)
        scalar_map.setdefault(_Any, AnyDefinition)
        scalar_map.setdefault(FieldSet, FieldSetDefinition)
        merged_config = dataclasses.replace(effective_config, scalar_map=scalar_map)

        roots = [query, mutation, subscription, *types]
        used_directive_names = _scan_used_federation_directive_names(
            [root for root in roots if root is not None]
        )
        link = Link(
            url=f"https://specs.apollo.dev/federation/v{federation_version}",
            import_=sorted(f"@{name}" for name in used_directive_names),
        )

        super().__init__(
            query=synthetic_query,
            mutation=mutation,
            subscription=subscription,
            types=types,
            config=merged_config,
            schema_directives=[link],
            **kwargs,
        )


__all__ = ["Schema"]
