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
from collections.abc import Callable, Sequence
from typing import Any, NewType

ID = NewType("ID", str)


@dataclasses.dataclass(frozen=True, kw_only=True)
class ScalarDefinition:
    """What `bramble.scalar(...)` produces -- how a Python type is represented on the wire.

    Registered by mapping the Python type to it in `SchemaConfig(scalar_map={...})`, rather than by
    wrapping the type itself, so the Python type stays an ordinary type that type checkers
    understand.
    """

    name: str | None
    description: str | None
    specified_by_url: str | None
    serialize: Callable[[Any], Any] | None
    parse_value: Callable[[Any], Any] | None
    parse_literal: Callable[[Any], Any] | None
    directives: tuple[object, ...]


def scalar(
    *,
    name: str | None = None,
    description: str | None = None,
    specified_by_url: str | None = None,
    serialize: Callable[[Any], Any] | None = None,
    parse_value: Callable[[Any], Any] | None = None,
    parse_literal: Callable[[Any], Any] | None = None,
    directives: Sequence[object] = (),
) -> ScalarDefinition:
    """Defines a custom GraphQL scalar for an existing Python type.

        Base64 = NewType("Base64", bytes)

        schema = bramble.Schema(
            query=Query,
            config=SchemaConfig(scalar_map={Base64: bramble.scalar(
                name="Base64",
                serialize=lambda value: base64.b64encode(value).decode(),
                parse_value=base64.b64decode,
            )}),
        )

    Arguments:
        name: the GraphQL scalar name. Defaults to the Python type's own `__name__`.
        description: rendered as the scalar's SDL description.
        specified_by_url: rendered as `@specifiedBy(url: ...)` and reported by introspection.
        serialize: converts a resolved Python value to what goes in the response.
        parse_value: converts an incoming argument/variable value to what the resolver receives.
        parse_literal: converts a literal written inline in the query document.
        directives: applied schema-directive instances, checked against `SCALAR`.

    Registration is only required to *declare* the scalar in SDL and introspection -- an
    unregistered custom type already round-trips through execution unchanged.
    """
    return ScalarDefinition(
        name=name,
        description=description,
        specified_by_url=specified_by_url,
        serialize=serialize,
        parse_value=parse_value,
        parse_literal=parse_literal,
        directives=tuple(directives),
    )


Upload = NewType("Upload", bytes)
"""A scalar for file upload arguments/fields. Entirely opaque -- `serialize`/`parse_value` are
both identity functions, so whatever object a request's transport layer puts into
`variable_values` (bytes, a file-like object, ...) passes straight through to the resolver
unchanged. `bramble.http` implements the GraphQL multipart request spec on top of this (see
`bramble.http.base.BaseView.parse_multipart_operations` and `docs/guides/file-upload.md`); a
caller driving `Schema.execute`/`execute_async` directly is free to populate `variable_values`
however it likes instead.
"""

UploadDefinition = scalar(
    name="Upload",
    description="Represents a file upload.",
    serialize=lambda value: value,
    parse_value=lambda value: value,
)
"""Register with `SchemaConfig(scalar_map={Upload: UploadDefinition})` for a `scalar Upload`
declaration (with its description) in `to_sdl()`'s output -- `Upload` used unregistered already
round-trips correctly through execution regardless, the same way bramble's other built-in
scalars (`datetime.datetime`, `decimal.Decimal`, ...) don't require `scalar_map` registration to
work, only to be declared in SDL.
"""
