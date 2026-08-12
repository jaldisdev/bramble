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

from __future__ import annotations

import dataclasses
import types as types_module
import typing
from collections.abc import AsyncGenerator, AsyncIterable, AsyncIterator, Callable, Sequence
from typing import Any

from bramble import _resolver
from bramble._bramble import (
    GraphQLTypeInfo,
    ParsedDocument,
    SchemaError,
    compile_schema,
    describe_union,
    render_sdl,
    resolve_persisted_query,
    validate_query,
)
from bramble._execution import execute as _execute
from bramble._execution import execute_async as _execute_async
from bramble._execution import execute_incremental as _execute_incremental
from bramble._execution import subscribe_async as _subscribe_async
from bramble._extension import SchemaExtension
from bramble._introspection import INTROSPECTION_TYPES
from bramble._lazy import LazyType, _lazy_reference_marker, namespace_for_callable, namespace_for_class
from bramble._private import is_private
from bramble._resolver import Streamable
from bramble._scalar import ScalarDefinition
from bramble._type import field as _field
from bramble._type import resolve_pending_types
from bramble._type import type as _type_decorator
from bramble._union import UnionDefinition
from bramble.schema.config import SchemaConfig

_type = type  # capture the builtin before it's shadowed by a `type` parameter's annotation

_CONTAINER_ORIGINS = (list, tuple, set, frozenset)

# The async wrappers a field's *declared* type is resolved through rather than to: Rust's
# `resolve_graphql_type` unwraps `AsyncGenerator[T, ...]`/`AsyncIterator[T]`/`AsyncIterable[T]` to
# `T` (a subscription root field yields one independent response per event) and `Streamable[T]` to
# `[T]` (a `@stream` field's items are elements of one list) -- see
# `crates/bramble-py/src/typing_utils.rs`'s `resolve_core`. Discovery has to look through exactly
# the same set: a type named by a field but never walked to is left out of `types_by_name`
# entirely, so the field renders fine in SDL while the schema has no such type to actually execute
# a selection set against. Keep this tuple in lockstep with `resolve_core`'s own unwrapping.
_ASYNC_ORIGINS = (AsyncGenerator, AsyncIterator, AsyncIterable, Streamable)


class _SchemaGraph:
    def __init__(self, localns: dict[str, _type]) -> None:
        # Seeded upfront with every root class (query/mutation/subscription/types) by name, so
        # `typing.get_type_hints` can resolve a field that forward-references one of them even
        # when they're all defined in the same enclosing local scope (as bramble's own tests do) --
        # `get_type_hints` only ever sees module globals otherwise, never an enclosing function's
        # locals. A field referencing some type reachable *only* transitively (not one of the
        # explicit roots) and defined locally can still fail to resolve; that's an accepted edge
        # the caller can route around by listing it in `types=[...]` too.
        self.localns = localns
        self.visited: set[_type] = set()
        self.types_by_name: dict[str, _type] = {}
        self.implementors_by_interface: dict[str, list[_type]] = {}
        self.unions_by_name: dict[str, Any] = {}
        # Execution (§5/§11) needs the real Python member classes (to run `resolve_type`'s
        # `isinstance`/return-value checks against) and the original marker object (to get at its
        # live `resolve_type` callable) -- neither survives in `UnionInfo` alone, which only
        # carries display-friendly member reprs and a `has_custom_resolve_type` bool.
        self.union_members_by_name: dict[str, list[_type]] = {}
        self.union_markers_by_name: dict[str, UnionDefinition | None] = {}
        # For SDL rendering (§6/§12): the *definitions* (locations/field defs) of every schema
        # directive actually applied somewhere in the graph, keyed by name. Rust's IR only keeps
        # each *application*'s values (`TypeDefinition`/`FieldDefinition.applied_directives`) --
        # reconstructing which distinct directive classes are in play, and their declared
        # locations/fields, needs the original Python classes, which only this graph walk has
        # visibility into.
        self.schema_directives_by_name: dict[str, Any] = {}


def _union_definition_marker(metadata: Sequence[Any]) -> UnionDefinition | None:
    for item in metadata:
        if isinstance(item, UnionDefinition):
            return item
    return None


def _unwrap_union_member(member: Any) -> Any:
    """Resolves one union member that was written as an `Annotated[...]` wrapper.

    A union whose members live in another module has to name them lazily -- that is the whole
    point of `bramble.lazy`, and a genuine import cycle leaves no alternative:

        MenuLinkable = Annotated[
            Union[MenuGroup, Annotated["Shortcut", bramble.lazy(".perspective")]],
            bramble.union("MenuLinkable"),
        ]

    Members used to be taken verbatim from `get_args`, so such a member arrived as the `Annotated`
    wrapper itself and was rejected as "not a '@bramble.type'-decorated object type". Resolving the
    lazy reference here is safe: this only runs while a `Schema()` is being built, by which point
    the referenced module is importable.
    """
    if typing.get_origin(member) is not typing.Annotated:
        return member
    inner, *metadata = typing.get_args(member)
    reference = _lazy_reference_marker(metadata)
    if reference is not None and isinstance(inner, typing.ForwardRef):
        return reference.resolve_forward_ref(inner).resolve_type()
    return inner


def _union_member_classes(annotation: Any) -> list[_type]:
    origin = typing.get_origin(annotation)
    if origin is typing.Union or origin is types_module.UnionType:
        return [
            _unwrap_union_member(member)
            for member in typing.get_args(annotation)
            if member is not type(None)
        ]
    return [_unwrap_union_member(annotation)]


def _register_union(
    describe_annotation: Any, members_source: Any, marker: UnionDefinition | None, *, graph: _SchemaGraph
) -> None:
    """`describe_annotation` (passed to `describe_union` as-is) is whatever carries the marker --
    the full `Annotated[...]` form when one was declared, so its name/description survive -- while
    `members_source` is always the plain `Union[...]`/single-type form the actual member classes
    are read off of (an `Annotated` wrapper's `get_origin` isn't `Union`, so extracting members
    from it directly would see one "member": the wrapper itself).
    """
    union_info = describe_union(describe_annotation)
    graph.unions_by_name[union_info.name] = union_info
    graph.union_members_by_name[union_info.name] = _union_member_classes(members_source)
    graph.union_markers_by_name[union_info.name] = marker


def _discover_annotation(annotation: Any, *, graph: _SchemaGraph) -> None:
    if isinstance(annotation, LazyType):
        # The one place the deferred import actually happens (§ circular imports): by
        # construction, this only ever runs once a `Schema()` is being built, well after the
        # module that originally wrote `bramble.lazy(...)` has finished loading -- unlike the
        # decoration-time resolution (Rust `read_fields`/etc.), which only ever sees the
        # placeholder's own name, never imports anything.
        _discover_annotation(annotation.resolve_type(), graph=graph)
        return

    origin = typing.get_origin(annotation)

    if origin is typing.Annotated:
        underlying, *metadata = typing.get_args(annotation)
        marker = _union_definition_marker(metadata)
        if marker is not None:
            _register_union(annotation, underlying, marker, graph=graph)
            # Recurse straight into the member classes themselves, *not* `underlying` -- if
            # `underlying` is itself a bare `Union[...]`, routing it back through
            # `_discover_annotation` would hit the plain-`Union` branch below and register it a
            # second time under an autogenerated name, duplicating the union this marker already
            # named explicitly.
            for member in _union_member_classes(underlying):
                _discover_annotation(member, graph=graph)
            return
        _discover_annotation(underlying, graph=graph)
        return

    if origin is typing.Union or origin is types_module.UnionType:
        members = [member for member in typing.get_args(annotation) if member is not type(None)]
        # A `Union` with exactly one non-`None` member is just `Optional[X]` -- a nullable
        # reference, not a GraphQL union type; only 2+ members need `resolve_type` dispatch
        # (matches `describe_union`'s own bare-`Union[...]` handling, tested directly against
        # its Rust implementation in `test_bare_union_gets_autogenerated_name`).
        if len(members) > 1:
            _register_union(annotation, annotation, None, graph=graph)
        for member in members:
            _discover_annotation(member, graph=graph)
        return

    if origin in _CONTAINER_ORIGINS or origin in _ASYNC_ORIGINS:
        # Recursing into every argument (rather than just `get_args(...)[0]`) is what makes
        # `AsyncGenerator[list[T], None]`/`AsyncIterator[T | None]` fall through to the container
        # and union branches above and resolve correctly. An `AsyncGenerator`'s own send-type
        # argument (`None`, or `NoneType` under `typing.AsyncGenerator`) needs no special case --
        # neither carries `__bramble_type_info__`, so walking it is already a no-op.
        for member in typing.get_args(annotation):
            _discover_annotation(member, graph=graph)
        return

    if origin is not None:
        # An unrecognized generic origin (dict, a custom generic, etc.) -- not walked further;
        # nothing in bramble's own examples needs it, and guessing at arbitrary generics'
        # "referenced types" isn't safe to do blindly.
        return

    if isinstance(annotation, _type) and hasattr(annotation, "__bramble_type_info__"):
        _discover_type(annotation, graph=graph)


def _register_schema_directive_definitions(directives: Sequence[Any], *, graph: _SchemaGraph) -> None:
    """Registers the *class* behind each applied schema-directive instance -- `directives` may
    include non-directive objects (ignored, matching `_validate_directive_locations`'s own
    tolerance) or plain values that were never validated as directives at all.
    """
    for directive in directives:
        info = getattr(_type(directive), "__bramble_directive_info__", None)
        if info is not None:
            graph.schema_directives_by_name[info.name] = info


def _discover_type(cls: _type, *, graph: _SchemaGraph) -> None:
    if cls in graph.visited:
        return
    graph.visited.add(cls)

    info = cls.__bramble_type_info__
    graph.types_by_name[info.name] = cls

    if info.kind == "enum":
        # An enum is a leaf: it has members, not fields, so there's nothing further to walk to --
        # and it isn't a dataclass either, so every step below (`dataclasses.fields`, the MRO
        # interface scan, `get_type_hints` over field annotations) would be wrong or outright
        # raise. Its own type-level directives still need registering, same as any other type's.
        _register_schema_directive_definitions(getattr(cls, "__bramble_applied_directives__", ()), graph=graph)
        for enum_value_info in info.enum_values:
            _register_schema_directive_definitions(
                getattr(cls, "__bramble_enum_value_directives__", {}).get(enum_value_info.name, ()), graph=graph
            )
        return

    _register_schema_directive_definitions(getattr(cls, "__bramble_applied_directives__", ()), graph=graph)
    for dataclass_field in dataclasses.fields(cls):
        _register_schema_directive_definitions(getattr(dataclass_field, "directives", ()), graph=graph)

    for base in cls.__mro__[1:]:
        base_info = getattr(base, "__bramble_type_info__", None)
        if base_info is not None and base_info.kind == "interface":
            graph.implementors_by_interface.setdefault(base_info.name, []).append(cls)
            # The interface itself might never be reachable via any field's own annotation (only
            # its implementors are, typically) -- register it here too, or the compatibility
            # check below would have an interface name with no corresponding type entry. Its own
            # type-level applied directives need the same treatment: `_discover_type` never runs
            # on `base` directly in that case, so nothing else would ever look at them.
            graph.types_by_name.setdefault(base_info.name, base)
            _register_schema_directive_definitions(getattr(base, "__bramble_applied_directives__", ()), graph=graph)

    localns = {**graph.localns, **namespace_for_class(cls)}
    try:
        hints = typing.get_type_hints(cls, localns=localns, include_extras=True)
    except NameError as error:
        raise SchemaError(f"could not resolve field annotations for '{cls.__name__}': {error}") from error

    for annotation in hints.values():
        # A `Private[T]` field is invisible to the GraphQL schema entirely (Rust's `read_fields`
        # already excludes it from this class's own `FieldDefinition`s) -- its own type shouldn't
        # get pulled into the schema graph either, just because it happened to appear here.
        if is_private(annotation):
            continue
        _discover_annotation(annotation, graph=graph)

    # `hints` above only ever covers the class's own dataclass FIELD annotations (a resolver's
    # *return* type, injected by `_ensure_field_annotations`) -- a type used only as one of a
    # resolver's own *arguments* (`def resolver(filter: SomeInput) -> ...`) never appears there,
    # since it's a function parameter, not a class attribute. Without this, such a type would
    # never make it into `types_by_name`/the compiled schema at all, silently breaking both query
    # validation (an input's own field shape never gets checked) and argument coercion (the
    # resolver would only ever receive a raw dict, never a real instance of the input class).
    for field_info in info.fields:
        if not field_info.has_resolver:
            continue
        resolver = getattr(cls, field_info.name, None)
        if resolver is None:
            continue
        resolver_localns = {**graph.localns, **namespace_for_callable(resolver)}
        try:
            resolver_hints = typing.get_type_hints(resolver, localns=resolver_localns, include_extras=True)
        except NameError:
            continue  # same graceful degradation as field-type resolution elsewhere
        for annotation in resolver_hints.values():
            _discover_annotation(annotation, graph=graph)


def _scalar_name(python_type: Any, scalar_definition: ScalarDefinition) -> str:
    """The GraphQL name a registered scalar resolves to: its explicit `name=`, or (matching
    `resolve_graphql_type`'s own fallback for an as-yet-unregistered scalar reference) the
    Python type's own `__name__` -- the convention `bramble.scalar()` callers follow by default.
    """
    if scalar_definition.name is not None:
        return scalar_definition.name
    return getattr(python_type, "__name__", str(python_type))


def _build_introspective_query(query: _type) -> _type:
    """Returns a subclass of `query` carrying the two introspection meta-fields (§4.5), so
    `__schema`/`__type` are ordinary registered fields rather than executor special cases -- the
    same synthesis `bramble.federation.Schema` does for `_service`/`_entities`.

    Both need an explicit `name=`: `auto_camel_case` treats a leading underscore as a word
    separator, which would otherwise mangle `__schema` into `Schema`. The subclass keeps the
    original's GraphQL name, so nothing downstream sees a renamed query root.
    """
    from bramble._introspection import __Schema, __Type, resolve_schema_field, resolve_type_field

    namespace: dict[str, Any] = {
        "__annotations__": {"__schema": __Schema, "__type": typing.Optional[__Type]},
        "__schema": _field(resolver=resolve_schema_field, name="__schema"),
        "__type": _field(resolver=resolve_type_field, name="__type"),
    }
    introspective = _type(f"_Introspective{query.__name__}", (query,), namespace)
    # `description=`/`directives=` are carried over explicitly: a re-decorated subclass starts from
    # the decorator's own arguments, not the base's `__bramble_type_info__`, so omitting them
    # silently drops the user's own query-type description and applied directives from SDL.
    return _type_decorator(
        introspective,
        name=query.__bramble_type_info__.name,
        description=query.__bramble_type_info__.description,
        directives=getattr(query, "__bramble_applied_directives__", ()),
    )


class Schema:
    """A compiled, executable GraphQL schema.

        @bramble.type
        class Query:
            @bramble.field
            def greet(name: str) -> str:
                return f"Hello, {name}!"

        schema = bramble.Schema(query=Query)
        schema.execute('{ greet(name: "Ada") }')

    Construction walks the whole type graph reachable from the root types, validates its shape
    (interface conformance, directive locations, name resolution), and compiles it once. Every
    subsequent request validates and executes against that compiled form rather than re-deriving
    anything, so building a `Schema` is the expensive step and should happen once at startup, not
    per request.

    The result is immutable in practice: mutating a decorated class after the fact will not be
    picked up.
    """

    def __init__(
        self,
        query: _type,
        mutation: _type | None = None,
        subscription: _type | None = None,
        directives: Sequence[Callable[..., Any]] = (),
        types: Sequence[_type] = (),
        extensions: Sequence[object] = (),
        config: SchemaConfig | None = None,
        default_context_factory: Callable[[], Any] | None = None,
        schema_directives: Sequence[object] = (),
    ) -> None:
        """Builds and validates the schema.

        Arguments:
            query: the query root type; required, and must be `@bramble.type`-decorated.
            mutation: the mutation root type, if the schema has one.
            subscription: the subscription root type, if the schema has one.
            directives: custom operation directive functions (`@bramble.directive`-decorated) that
                queries may then use, e.g. `@shout`.
            types: extra types to include even when no field's return type or resolver argument
                reaches them -- most often an interface implementor that is only ever returned as
                the interface.
            extensions: `bramble.SchemaExtension` subclasses (or instances) with lifecycle hooks
                around parsing, validation, execution, and each field's resolution -- see
                `docs/guides/extensions.md`.
            config: a `SchemaConfig` controlling naming, custom scalars, and batching.
            default_context_factory: called with no arguments to produce `info.context` for any
                execution that doesn't pass `context=` explicitly. (Deliberately *not* named
                `execution_context_class`, which means something different in Strawberry.)
            schema_directives: applied schema-directive instances attached to the `schema { ... }`
                block itself, e.g. federation's `@link`.

        Raises `bramble.SchemaError` for any schema-shape problem found during the build.
        """
        # Types whose annotations could not be resolved at decoration time (a resolver returning
        # a type from a module that had not finished importing) were deferred -- every module is
        # imported by now, so finish them before anything reads `__bramble_type_info__`.
        resolve_pending_types()

        if getattr(query, "__bramble_type_info__", None) is None:
            raise SchemaError("Schema(query=...) must be a @bramble.type-decorated class")

        for extension in extensions:
            candidate = extension if isinstance(extension, _type) else _type(extension)
            if not issubclass(candidate, SchemaExtension):
                raise SchemaError(
                    f"'{candidate.__name__}' passed to Schema(extensions=...) is not a "
                    "bramble.SchemaExtension subclass"
                )

        for directive_function in directives:
            if getattr(directive_function, "__bramble_directive_info__", None) is None:
                function_name = getattr(directive_function, "__name__", directive_function)
                raise SchemaError(f"'{function_name}' passed to Schema(directives=...) is not a @bramble.directive")

        # Distinct from `directives=` above (custom *operation* directive functions):
        # `schema_directives=` holds applied schema-directive *instances* (e.g. `Link(url=...)`),
        # attached to the `schema { ... }` block itself rather than any type/field -- there is no
        # `@bramble.type`-decorated class for the schema itself to hang these off of, unlike every
        # other applied-directive site.
        for schema_directive_instance in schema_directives:
            if getattr(schema_directive_instance, "__bramble_directive_info__", None) is None:
                raise SchemaError(
                    f"'{schema_directive_instance}' passed to Schema(schema_directives=...) is not a "
                    "@bramble.schema_directive instance"
                )

        # Every schema is introspectable (§4.5), so the meta-fields are injected unconditionally
        # rather than opted into. `types=` gains the introspection types themselves: most are
        # reachable by following the injected fields' own annotations, but listing them makes the
        # set independent of how the graph walker happens to traverse.
        #
        # `self.query` deliberately stays the caller's *own* class -- that's the documented,
        # tested contract (`schema.query is Query`), and it keeps the injection invisible to
        # anything reflecting over the schema. Execution and the type-graph walk use
        # `self.query_root` instead, which is the subclass actually carrying `__schema`/`__type`.
        query_root = _build_introspective_query(query)
        types = (*types, *INTROSPECTION_TYPES)

        self.query = query
        self.query_root = query_root
        self.mutation = mutation
        self.subscription = subscription
        self.directives = tuple(directives)
        self.types = tuple(types)
        self.extensions = tuple(extensions)
        self.config = config if config is not None else SchemaConfig()
        self.default_context_factory = default_context_factory
        self.schema_directives = tuple(schema_directives)

        roots = [root for root in (query_root, mutation, subscription, *types) if root is not None]
        localns = {root.__name__: root for root in roots}
        graph = _SchemaGraph(localns)

        for root in roots:
            _discover_type(root, graph=graph)

        # A custom operation directive is a standalone function, never attached to any of the
        # `roots` above -- an input type used only as one of its own arguments (never as a
        # resolver argument or a field's return type) would otherwise be invisible to
        # `types_by_name`, the same gap Task 90 found and fixed for resolver arguments.
        for directive_function in directives:
            directive_localns = {**graph.localns, **namespace_for_callable(directive_function)}
            try:
                directive_hints = typing.get_type_hints(
                    directive_function, localns=directive_localns, include_extras=True
                )
            except NameError:
                continue
            for annotation in directive_hints.values():
                _discover_annotation(annotation, graph=graph)

        # Interface conformance and name resolution are validated inside `compile_schema` (Rust),
        # which is the boundary that owns schema *shape* -- see
        # `bramble_core::schema::validate_schema_shape`. Nothing to do here.

        # `schema_directives=[...]` instances (e.g. `Link(url=...)`) are applied to the schema
        # block itself, never attached to any type/field the graph walk above already visits --
        # their *declarations* need registering here explicitly, the same way the walk registers
        # every other applied directive's declaration as it's discovered.
        _register_schema_directive_definitions(self.schema_directives, graph=graph)

        # The compiled schema: assembled and validated once, here, per §7b -- every subsequent
        # request's parse/validate/execute cycle (Tasks 9/11) operates against this, not against
        # the decorators' isolated per-class registrations.
        self.types_by_name = graph.types_by_name
        self.implementors_by_interface = graph.implementors_by_interface
        self.unions_by_name = graph.unions_by_name
        self.union_members_by_name = graph.union_members_by_name
        self.union_markers_by_name = graph.union_markers_by_name
        self.schema_directives_by_name = graph.schema_directives_by_name
        self.scalars_by_python_type = dict(self.config.scalar_map)
        self.directive_functions_by_name = {
            directive_function.__bramble_directive_info__.name: directive_function
            for directive_function in self.directives
        }

        scalar_names = [
            _scalar_name(python_type, scalar_definition)
            for python_type, scalar_definition in self.scalars_by_python_type.items()
        ]
        self.scalars_by_name = {
            _scalar_name(python_type, scalar_definition): scalar_definition
            for python_type, scalar_definition in self.scalars_by_python_type.items()
        }
        scalar_directives = [
            (_scalar_name(python_type, scalar_definition), scalar_definition.directives)
            for python_type, scalar_definition in self.scalars_by_python_type.items()
        ]
        scalar_descriptions = [
            (_scalar_name(python_type, scalar_definition), scalar_definition.description)
            for python_type, scalar_definition in self.scalars_by_python_type.items()
        ]

        self._compiled = compile_schema(
            query_type_name=query.__bramble_type_info__.name,
            mutation_type_name=mutation.__bramble_type_info__.name if mutation is not None else None,
            subscription_type_name=(
                subscription.__bramble_type_info__.name if subscription is not None else None
            ),
            types=[cls.__bramble_type_info__ for cls in graph.types_by_name.values()],
            unions=list(graph.unions_by_name.values()),
            directives=[directive.__bramble_directive_info__ for directive in self.directives],
            schema_directives=list(graph.schema_directives_by_name.values()),
            scalar_names=scalar_names,
            scalar_directives=scalar_directives,
            scalar_descriptions=scalar_descriptions,
            auto_camel_case=self.config.auto_camel_case,
            schema_applied_directives=list(self.schema_directives),
        )

    def validate_query(self, query: str, *, operation_name: str | None = None) -> None:
        """Validates `query`'s (optionally named) operation against this compiled schema (§7a),
        raising a `bramble.GraphQLError` on the first violation found. Returns `None` if valid.

        Deliberately unaffected by `SchemaConfig(validate_queries=False)`: that switch says "don't
        validate on the way to executing", whereas calling this *is* the request to validate --
        which is what makes it the tool for finding what a schema running unvalidated would break
        on before turning validation back on.
        """
        validate_query(query, self._compiled, operation_name)

    def type_for(self, graphql_type: "GraphQLTypeInfo | str") -> _type | None:
        """The `@bramble.type`/`interface`/`input`/`enum`-decorated class behind a GraphQL type,
        named either by a plain type name or by a `GraphQLTypeInfo` -- which is what `Info` carries
        for the field currently being resolved:

            @bramble.field
            def something(info: bramble.Info) -> str:
                returned = info.schema.type_for(info.return_type)

        `NonNull`/`List` wrapping is unwrapped first, so `[Post!]!` resolves to `Post`. Returns
        `None` for a type this schema has no Python class for: a scalar (whose class is registered
        via `SchemaConfig(scalar_map=...)`, not walked into the type graph), a union (which is an
        annotation over member classes rather than a class of its own -- see `union_members_by_name`),
        or a name this schema doesn't know at all.
        """
        if isinstance(graphql_type, str):
            return self.types_by_name.get(graphql_type)

        type_info: Any = graphql_type
        while type_info.kind in ("NON_NULL", "LIST"):
            type_info = type_info.of_type
        return self.types_by_name.get(type_info.name) if type_info.name is not None else None

    def applied_directives_for_type(self, graphql_type: "_type | GraphQLTypeInfo | str") -> tuple[object, ...]:
        """The schema-directive instances applied to a type -- `@bramble.type(directives=[...])`'s
        own arguments, as the live instances, in declaration order.

        Accepts the decorated class itself, a type name, or the `GraphQLTypeInfo` a field's
        `Info.return_type` carries (resolved through `type_for`). Empty for a type with no applied
        directives, and for anything `type_for` can't resolve to a class.

        Schema directives carry no execution behaviour of their own (that's what separates them from
        `bramble.directive`) -- this is the supported way to build that behaviour yourself, in a
        `SchemaExtension`, without reaching into bramble's internals.
        """
        resolved = graphql_type if isinstance(graphql_type, _type) else self.type_for(graphql_type)
        if resolved is None:
            return ()
        return tuple(getattr(resolved, "__bramble_applied_directives__", ()))

    def applied_directives_for_field(self, parent_type: "_type | str", python_name: str) -> tuple[object, ...]:
        """The schema-directive instances applied to one field -- `bramble.field(directives=[...])`'s
        own arguments, as the live instances, in declaration order.

        `parent_type` is the class the field is declared on (`Info.parent_type`) or its GraphQL name;
        `python_name` is the field's Python identifier (`Info.python_name`), not its camelCased
        GraphQL name -- `Info` carries both, and matching on the Python one keeps this independent of
        `auto_camel_case`. Inherited fields count: a field an interface declares is readable through
        any implementor. Empty for an unknown type or field, or one with no applied directives.
        """
        resolved = parent_type if isinstance(parent_type, _type) else self.types_by_name.get(parent_type)
        if resolved is None or not dataclasses.is_dataclass(resolved):
            return ()
        for dataclass_field in dataclasses.fields(resolved):
            if dataclass_field.name == python_name:
                return tuple(getattr(dataclass_field, "directives", ()))
        return ()

    def resolve_persisted_query(
        self,
        sha256_hash: str,
        *,
        query: str | None = None,
        operation_name: str | None = None,
    ) -> bool:
        """Implements the Automatic Persisted Queries protocol (§10) against this schema's cache.

        Returns `True` if `sha256_hash` was already cached, `False` if `query` was freshly
        parsed/validated and just registered under its hash. Raises `bramble.GraphQLError` with
        `code=PERSISTED_QUERY_NOT_FOUND` on a hash-only miss (the client should resend with
        `query` included) or `code=PERSISTED_QUERY_MISMATCH` if a provided `query`'s hash doesn't
        match `sha256_hash`.

        Use `prepare_persisted_query` instead when you intend to execute the result: it returns the
        cached document alongside this flag, and passing that document to `execute_async` is what
        actually lets a cache hit skip re-parsing and re-validating.
        """
        return self.prepare_persisted_query(
            sha256_hash, query=query, operation_name=operation_name
        ).cache_hit

    def prepare_persisted_query(
        self,
        sha256_hash: str,
        *,
        query: str | None = None,
        operation_name: str | None = None,
    ) -> Any:
        """The Automatic Persisted Queries protocol (§10), returning the cached document rather than
        just a hit/miss flag -- raising the same `PERSISTED_QUERY_NOT_FOUND`/`PERSISTED_QUERY_MISMATCH`
        errors as `resolve_persisted_query`.

        The result carries `.cache_hit` (a bool) and `.document`, an opaque handle to the parsed,
        already-validated document. Pass that handle as `document=` to `execute_async`/
        `execute_incremental`/`subscribe_async` to execute it without parsing or validating the
        query text a second time -- which is the entire point of persisting it.
        """
        return resolve_persisted_query(
            sha256_hash,
            self._compiled,
            query=query,
            operation_name=operation_name,
            validate=self.config.validate_queries,
        )

    async def execute_async(
        self,
        query: str,
        *,
        variable_values: dict[str, Any] | None = None,
        context: Any = None,
        root_value: Any = None,
        operation_name: str | None = None,
        resolved_dependencies: dict[Callable[..., Any], Any] | None = None,
        document: ParsedDocument | None = None,
    ) -> dict[str, Any]:
        """Executes `query` against this schema (§7a/§8/§11), returning a spec-shaped
        `{"data": ..., "errors": [...]}` response. See `bramble._execution.execute_async`.

        `resolved_dependencies` (§3c) pre-seeds this request's dependency-injection cache, keyed by
        provider-callable identity -- a value supplied this way is used without its provider ever
        being called, and (since bramble never owned it) never torn down by bramble either.
        """
        return await _execute_async(
            self,
            query,
            variable_values=variable_values,
            context=context,
            root_value=root_value,
            operation_name=operation_name,
            resolved_dependencies=resolved_dependencies,
            document=document,
        )

    def execute(
        self,
        query: str,
        *,
        variable_values: dict[str, Any] | None = None,
        context: Any = None,
        root_value: Any = None,
        operation_name: str | None = None,
        resolved_dependencies: dict[Callable[..., Any], Any] | None = None,
        document: ParsedDocument | None = None,
    ) -> dict[str, Any]:
        """Synchronous convenience wrapper around `execute_async` -- see its own docstring for the
        caveat about not being callable from within an already-running event loop.
        """
        return _execute(
            self,
            query,
            variable_values=variable_values,
            context=context,
            root_value=root_value,
            operation_name=operation_name,
            resolved_dependencies=resolved_dependencies,
            document=document,
        )

    async def execute_incremental(
        self,
        query: str,
        *,
        variable_values: dict[str, Any] | None = None,
        context: Any = None,
        root_value: Any = None,
        operation_name: str | None = None,
        resolved_dependencies: dict[Callable[..., Any], Any] | None = None,
        document: ParsedDocument | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Executes a query/mutation operation using `@defer`/`@stream`, yielding the initial
        `{"data": ..., "hasNext": bool}` payload followed by zero or more `{"incremental": [...],
        "hasNext": bool}` patches. See `bramble._execution.execute_incremental` for the concrete
        payload shape/scope this implements. Async-only, like `subscribe_async` -- an incremental
        delivery is as open-ended as a subscription's own event stream, so there's no synchronous
        `asyncio.run`-based convenience wrapper here either. A query/mutation with no active
        `@defer`/`@stream` marker should go through `execute_async` instead -- it's the
        zero-overhead path for that overwhelmingly common case.

        `resolved_dependencies` (§3c) -- see `execute_async`'s own docstring.
        """
        generator = _execute_incremental(
            self,
            query,
            variable_values=variable_values,
            context=context,
            root_value=root_value,
            operation_name=operation_name,
            resolved_dependencies=resolved_dependencies,
            document=document,
        )
        try:
            async for response in generator:
                yield response
        finally:
            # `async for` alone does *not* propagate an early `.aclose()` (a consumer stopping
            # iteration on *this* wrapper generator) down to `generator` itself -- without this
            # explicit `finally`, the inner generator would only ever get closed later, by
            # Python's own async-generator GC finalizer, not synchronously as part of this
            # generator's own shutdown. That's a real gap once a dependency's own generator-based
            # provider (§3c) is involved: its teardown needs to run reliably and promptly, not
            # "eventually, whenever GC gets to it."
            await generator.aclose()

    async def subscribe_async(
        self,
        query: str,
        *,
        variable_values: dict[str, Any] | None = None,
        context: Any = None,
        root_value: Any = None,
        operation_name: str | None = None,
        resolved_dependencies: dict[Callable[..., Any], Any] | None = None,
        document: ParsedDocument | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Executes a subscription operation, yielding one spec-shaped `{"data": ..., "errors":
        [...]}` response per event. See `bramble._execution.subscribe_async`. Async-only -- unlike
        `execute`/`execute_async`, there's no synchronous convenience wrapper, since a subscription
        is an open-ended event stream, not a single value `asyncio.run` could meaningfully block on.

        `resolved_dependencies` (§3c) -- see `execute_async`'s own docstring.
        """
        generator = _subscribe_async(
            self,
            query,
            variable_values=variable_values,
            context=context,
            root_value=root_value,
            operation_name=operation_name,
            resolved_dependencies=resolved_dependencies,
            document=document,
        )
        try:
            async for response in generator:
                yield response
        finally:
            # See `execute_incremental`'s identical `finally` above for why this is needed: without
            # it, a client unsubscribing/disconnecting only closes *this* wrapper generator, never
            # `generator` itself -- so a `Depends` provider's own generator-based teardown (§3c)
            # wouldn't run promptly on disconnect, only eventually via GC.
            await generator.aclose()

    def to_sdl(self) -> str:
        """Renders this schema's GraphQL SDL (§6/§9/§12): every reachable type/union/scalar/enum,
        plus the operation and schema directives actually in use. See `bramble_core::sdl::render_sdl`'s
        own doc comment for the remaining documented rendering gaps.
        """
        return render_sdl(self._compiled)

    def __str__(self) -> str:
        """The schema's SDL -- so `print(schema)` renders it. See `to_sdl`."""
        return self.to_sdl()


# `bramble._resolver.Info` annotates `schema: "Schema"`, but `_resolver` cannot import this module
# (the dependency runs the other way: `_schema` -> `_execution` -> `_resolver`). Binding the name
# here, once the class actually exists, is what makes `typing.get_type_hints(Info)` resolve instead
# of raising `NameError` -- a `TYPE_CHECKING`-only import would satisfy a type checker while leaving
# the runtime annotation dangling. Importing this module is unconditional (`bramble/__init__` does
# it), so the name is always bound before anything can observe it missing.
_resolver.Schema = Schema
