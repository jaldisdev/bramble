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

from collections.abc import Sequence
from typing import Any

from bramble._error import ErrorCode, GraphQLError


def _matches(candidate: type, obj: Any, info: Any) -> bool:
    is_type_of = getattr(candidate, "is_type_of", None)
    if is_type_of is not None:
        result = is_type_of(obj, info)
        # A returned *type* answers "which one is it" rather than "is it this one". Unlike a union,
        # an interface has a shared base to hang a single hook on, so declaring `is_type_of` once
        # there and returning the concrete class is the natural way to express one decision -- but
        # every implementor inherits that same method, and read as a boolean each one returns a
        # truthy class, so every candidate matches and resolution fails as ambiguous. Comparing the
        # returned type against the candidate makes exactly one match, and leaves the per-type
        # boolean form behaving exactly as before.
        if isinstance(result, type):
            return result is candidate
        return result
    return isinstance(obj, candidate)


def resolve_interface_type(candidates: Sequence[type], obj: Any, info: Any) -> type:
    """Determines which of an interface's implementing types a resolved value is.

    A value tagged by `bramble.cast(...)` short-circuits this: it names its own concrete type, for
    the case where neither `is_type_of` nor `isinstance` can identify it (a dict or ORM row standing
    in for a GraphQL type). Otherwise each candidate's `is_type_of` classmethod is tried, falling
    back to an `isinstance` check for candidates that declare none, per §4. Exactly one candidate
    must match -- zero or more than one is an execution-time error, not something to guess at.

    `is_type_of` may answer in either of two ways: a boolean, declared per implementing type
    ("is the value one of me?"), or the concrete type itself, which lets a single hook on the
    shared interface decide for all of its implementors at once. The two forms can be mixed
    freely across an interface's candidates.
    """
    tagged = getattr(obj, "__bramble_concrete_type__", None)
    if tagged is not None and tagged in candidates:
        return tagged

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
