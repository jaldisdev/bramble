from __future__ import annotations

import types as types_module
import typing
from collections.abc import Callable, Sequence
from typing import Any

from bramble._bramble import SchemaError, compile_schema, describe_union, validate_query
from bramble._scalar import ScalarDefinition
from bramble._union import UnionDefinition
from bramble.schema.config import SchemaConfig

_type = type  # capture the builtin before it's shadowed by a `type` parameter's annotation

_CONTAINER_ORIGINS = (list, tuple, set, frozenset)


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


def _union_definition_marker(metadata: Sequence[Any]) -> UnionDefinition | None:
    for item in metadata:
        if isinstance(item, UnionDefinition):
            return item
    return None


def _discover_annotation(annotation: Any, *, graph: _SchemaGraph) -> None:
    origin = typing.get_origin(annotation)

    if origin is typing.Annotated:
        underlying, *metadata = typing.get_args(annotation)
        if _union_definition_marker(metadata) is not None:
            union_info = describe_union(annotation)
            graph.unions_by_name[union_info.name] = union_info
        _discover_annotation(underlying, graph=graph)
        return

    if origin is typing.Union or origin is types_module.UnionType:
        for member in typing.get_args(annotation):
            if member is not type(None):
                _discover_annotation(member, graph=graph)
        return

    if origin in _CONTAINER_ORIGINS:
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


def _discover_type(cls: _type, *, graph: _SchemaGraph) -> None:
    if cls in graph.visited:
        return
    graph.visited.add(cls)

    info = cls.__bramble_type_info__
    graph.types_by_name[info.name] = cls

    for base in cls.__mro__[1:]:
        base_info = getattr(base, "__bramble_type_info__", None)
        if base_info is not None and base_info.kind == "interface":
            graph.implementors_by_interface.setdefault(base_info.name, []).append(cls)
            # The interface itself might never be reachable via any field's own annotation (only
            # its implementors are, typically) -- register it here too, or the compatibility
            # check below would have an interface name with no corresponding type entry.
            graph.types_by_name.setdefault(base_info.name, base)

    try:
        hints = typing.get_type_hints(cls, localns=graph.localns, include_extras=True)
    except NameError as error:
        raise SchemaError(f"could not resolve field annotations for '{cls.__name__}': {error}") from error

    for annotation in hints.values():
        _discover_annotation(annotation, graph=graph)


def _validate_interface_implementations(graph: _SchemaGraph) -> None:
    """Per §4/§8b: an interface's field contract is checked once the whole graph is known. Since
    bramble has implementing types inherit from the interface directly (no `implements=[...]`),
    dataclass field inheritance already makes outright field *omission* structurally impossible --
    so the checks that actually matter are covariance ones a subclass *can* still violate by
    re-annotating a field: weakening a non-null interface field to nullable, or adding a new
    required argument an interface field doesn't declare (matches graphql-core's own interface-
    conformance check, `validate_type_implements_interface`).
    """
    for interface_name, implementors in graph.implementors_by_interface.items():
        interface_cls = graph.types_by_name[interface_name]
        interface_fields = {field.name: field for field in interface_cls.__bramble_type_info__.fields}

        for implementor in implementors:
            implementor_info = implementor.__bramble_type_info__
            implementor_fields = {field.name: field for field in implementor_info.fields}

            for field_name, interface_field in interface_fields.items():
                implementor_field = implementor_fields.get(field_name)
                if implementor_field is None:
                    raise SchemaError(
                        f"'{implementor_info.name}' does not implement field '{field_name}' "
                        f"declared by interface '{interface_name}'"
                    )

                if interface_field.is_nullable is False and implementor_field.is_nullable is True:
                    raise SchemaError(
                        f"'{implementor_info.name}.{field_name}' is nullable, but interface "
                        f"'{interface_name}' declares it as non-null"
                    )

                interface_argument_names = {argument.name for argument in interface_field.arguments}
                for argument in implementor_field.arguments:
                    if (
                        argument.name not in interface_argument_names
                        and not argument.is_nullable
                        and not argument.has_default
                    ):
                        raise SchemaError(
                            f"'{implementor_info.name}.{field_name}' adds required argument "
                            f"'{argument.name}' not declared by interface '{interface_name}'"
                        )


def _scalar_name(python_type: Any, scalar_definition: ScalarDefinition) -> str:
    """The GraphQL name a registered scalar resolves to: its explicit `name=`, or (matching
    `resolve_graphql_type`'s own fallback for an as-yet-unregistered scalar reference) the
    Python type's own `__name__` -- the convention `bramble.scalar()` callers follow by default.
    """
    if scalar_definition.name is not None:
        return scalar_definition.name
    return getattr(python_type, "__name__", str(python_type))


class Schema:
    def __init__(
        self,
        query: _type,
        mutation: _type | None = None,
        subscription: _type | None = None,
        directives: Sequence[Callable[..., Any]] = (),
        types: Sequence[_type] = (),
        extensions: Sequence[object] = (),
        config: SchemaConfig | None = None,
        execution_context_class: _type | None = None,
    ) -> None:
        if getattr(query, "__bramble_type_info__", None) is None:
            raise SchemaError("Schema(query=...) must be a @bramble.type-decorated class")

        for directive_function in directives:
            if getattr(directive_function, "__bramble_directive_info__", None) is None:
                function_name = getattr(directive_function, "__name__", directive_function)
                raise SchemaError(f"'{function_name}' passed to Schema(directives=...) is not a @bramble.directive")

        self.query = query
        self.mutation = mutation
        self.subscription = subscription
        self.directives = tuple(directives)
        self.types = tuple(types)
        self.extensions = tuple(extensions)
        self.config = config if config is not None else SchemaConfig()
        self.execution_context_class = execution_context_class

        roots = [root for root in (query, mutation, subscription, *types) if root is not None]
        localns = {root.__name__: root for root in roots}
        graph = _SchemaGraph(localns)

        for root in roots:
            _discover_type(root, graph=graph)

        _validate_interface_implementations(graph)

        # The compiled schema: assembled and validated once, here, per §7b -- every subsequent
        # request's parse/validate/execute cycle (Tasks 9/11) operates against this, not against
        # the decorators' isolated per-class registrations.
        self.types_by_name = graph.types_by_name
        self.implementors_by_interface = graph.implementors_by_interface
        self.unions_by_name = graph.unions_by_name
        self.scalars_by_python_type = dict(self.config.scalar_map)

        scalar_names = [
            _scalar_name(python_type, scalar_definition)
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
            scalar_names=scalar_names,
        )

    def validate_query(self, query: str, *, operation_name: str | None = None) -> None:
        """Validates `query`'s (optionally named) operation against this compiled schema (§7a),
        raising a `bramble.GraphQLError` on the first violation found. Returns `None` if valid.
        """
        validate_query(query, self._compiled, operation_name)
