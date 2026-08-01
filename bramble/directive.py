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

import enum
from collections.abc import Callable, Sequence
from typing import Any, Generic, TypeVar

from bramble._bramble import SchemaError, describe_operation_directive

T = TypeVar("T")


class DirectiveLocation(enum.Enum):
    QUERY = "QUERY"
    MUTATION = "MUTATION"
    SUBSCRIPTION = "SUBSCRIPTION"
    FIELD = "FIELD"
    FRAGMENT_DEFINITION = "FRAGMENT_DEFINITION"
    FRAGMENT_SPREAD = "FRAGMENT_SPREAD"
    INLINE_FRAGMENT = "INLINE_FRAGMENT"


class DirectiveValue(Generic[T]):
    """Marker used in a custom operation directive's parameter annotation to receive the field's
    already-resolved value. Never instantiated -- only ever appears as a type annotation.
    """


def directive(
    locations: Sequence[DirectiveLocation],
    *,
    name: str | None = None,
    description: str | None = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    def wrap(func: Callable[..., Any]) -> Callable[..., Any]:
        func.__bramble_directive_info__ = describe_operation_directive(
            func,
            locations=[location.value for location in locations],
            name=name,
            description=description,
        )
        return func

    return wrap


def apply_directive(
    directive_function: Callable[..., Any],
    value: Any,
    arguments: dict[str, Any] | None = None,
) -> Any:
    """Applies one custom operation directive to an already-resolved value (§7), binding it to
    whichever parameter was classified as the `DirectiveValue[T]` and the given query-supplied
    arguments to the rest. Chaining several directives on the same field
    (`@replace(...) @include(if: $x)`) is just calling this repeatedly, feeding each result into
    the next -- there's no separate "chain" mechanism to build.
    """
    info = getattr(directive_function, "__bramble_directive_info__", None)
    if info is None:
        function_name = getattr(directive_function, "__name__", directive_function)
        raise SchemaError(f"'{function_name}' is not a @bramble.directive")

    kwargs = dict(arguments or {})
    if info.value_parameter is not None:
        kwargs[info.value_parameter] = value

    return directive_function(**kwargs)
