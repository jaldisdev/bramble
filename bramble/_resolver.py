from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Generic, TypeVar

ParentType = TypeVar("ParentType")
ContextType = TypeVar("ContextType")
RootValueType = TypeVar("RootValueType")


class Parent(Generic[ParentType]):
    """Marker used in a resolver parameter's annotation to receive the parent/root value.

    Never instantiated -- `Parent[T]` only ever appears as a type annotation
    (`def resolver(parent: Parent[User]) -> str: ...`); the annotation itself is what the
    Rust-side signature classifier looks for.
    """


class Info(Generic[ContextType, RootValueType]):
    """Marker used in a resolver parameter's annotation to receive the execution context.

    Populated by the execution bridge for each resolver call; not constructible here.
    """

    field_name: str
    python_name: str
    context: ContextType
    root_value: RootValueType
    variable_values: dict[str, Any]
    query: str | None
    path: "Path"
    selected_fields: list["SelectedField"]
    schema: "Schema"


class Argument:
    def __init__(
        self,
        name: str | None = None,
        description: str | None = None,
        deprecation_reason: str | None = None,
        graphql_type: Any | None = None,
        directives: Sequence[object] = (),
    ) -> None:
        self.name = name
        self.description = description
        self.deprecation_reason = deprecation_reason
        self.graphql_type = graphql_type
        self.directives = tuple(directives)


def argument(
    name: str | None = None,
    description: str | None = None,
    deprecation_reason: str | None = None,
    graphql_type: Any | None = None,
    directives: Sequence[object] = (),
) -> Argument:
    return Argument(
        name=name,
        description=description,
        deprecation_reason=deprecation_reason,
        graphql_type=graphql_type,
        directives=directives,
    )
