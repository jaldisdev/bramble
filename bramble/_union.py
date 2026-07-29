from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from bramble._error import ErrorCode, GraphQLError


class UnionDefinition:
    def __init__(
        self,
        name: str,
        description: str | None = None,
        resolve_type: Callable[[Any, Any], type] | None = None,
    ) -> None:
        self.name = name
        self.description = description
        self.resolve_type = resolve_type


def union(
    name: str,
    description: str | None = None,
    resolve_type: Callable[[Any, Any], type] | None = None,
) -> UnionDefinition:
    return UnionDefinition(name=name, description=description, resolve_type=resolve_type)


def resolve_union_type(
    definition: UnionDefinition | None,
    members: Sequence[type],
    obj: Any,
    info: Any,
) -> type:
    """Determines which of a union's member types a resolved value is.

    Uses the union's custom `resolve_type` callback if one was declared (per §5, a bramble
    addition since a union has no shared base class to hang a per-member `is_type_of` on);
    otherwise falls back to an `isinstance` check against each member, same as interfaces.
    """
    if definition is not None and definition.resolve_type is not None:
        resolved = definition.resolve_type(obj, info)
        if resolved not in members:
            member_names = ", ".join(member.__name__ for member in members)
            raise GraphQLError(
                f"resolve_type returned '{getattr(resolved, '__name__', resolved)}', which is "
                f"not a member of this union ({member_names})",
                code=ErrorCode.UNION_TYPE_RESOLUTION_FAILED,
            )
        return resolved

    matches = [member for member in members if isinstance(obj, member)]

    if not matches:
        member_names = ", ".join(member.__name__ for member in members)
        raise GraphQLError(
            f"no member type matched the resolved value (tried: {member_names})",
            code=ErrorCode.UNION_TYPE_RESOLUTION_FAILED,
        )
    if len(matches) > 1:
        match_names = ", ".join(member.__name__ for member in matches)
        raise GraphQLError(
            f"resolved value matched more than one union member: {match_names}",
            code=ErrorCode.UNION_TYPE_RESOLUTION_FAILED,
        )

    return matches[0]
