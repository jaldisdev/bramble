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
