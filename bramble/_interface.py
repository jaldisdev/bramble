from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from bramble._error import ErrorCode, GraphQLError


def _matches(candidate: type, obj: Any, info: Any) -> bool:
    is_type_of = getattr(candidate, "is_type_of", None)
    if is_type_of is not None:
        return is_type_of(obj, info)
    return isinstance(obj, candidate)


def resolve_interface_type(candidates: Sequence[type], obj: Any, info: Any) -> type:
    """Determines which of an interface's implementing types a resolved value is.

    Tries each candidate's `is_type_of` classmethod (falling back to an `isinstance` check
    against candidates that declare none) in order, per §4. Exactly one candidate must match --
    zero or more than one is an execution-time error, not something to silently guess at.
    """
    matches = [candidate for candidate in candidates if _matches(candidate, obj, info)]

    if not matches:
        candidate_names = ", ".join(candidate.__name__ for candidate in candidates)
        raise GraphQLError(
            f"no implementing type matched the resolved value (tried: {candidate_names})",
            code=ErrorCode.INTERFACE_TYPE_RESOLUTION_FAILED,
        )

    if len(matches) > 1:
        match_names = ", ".join(candidate.__name__ for candidate in matches)
        raise GraphQLError(
            f"resolved value matched more than one implementing type: {match_names}",
            code=ErrorCode.INTERFACE_TYPE_RESOLUTION_FAILED,
        )

    return matches[0]
