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
import enum
import sys
from collections.abc import Sequence
from typing import Any

from bramble._bramble import describe_schema_directive

_type = type  # capture the builtin before it's shadowed by a `type` parameter's annotation


class Location(enum.Enum):
    SCHEMA = "SCHEMA"
    SCALAR = "SCALAR"
    OBJECT = "OBJECT"
    FIELD_DEFINITION = "FIELD_DEFINITION"
    ARGUMENT_DEFINITION = "ARGUMENT_DEFINITION"
    INTERFACE = "INTERFACE"
    UNION = "UNION"
    ENUM = "ENUM"
    ENUM_VALUE = "ENUM_VALUE"
    INPUT_OBJECT = "INPUT_OBJECT"
    INPUT_FIELD_DEFINITION = "INPUT_FIELD_DEFINITION"


class DirectiveField(dataclasses.Field):
    def __init__(self, name: str, *, default: Any = dataclasses.MISSING) -> None:
        kwargs: dict[str, Any] = {"kw_only": True}
        if sys.version_info >= (3, 14):
            kwargs["doc"] = None

        super().__init__(
            default=default,
            default_factory=dataclasses.MISSING,
            init=True,
            repr=True,
            compare=True,
            hash=None,
            metadata=None,
            **kwargs,
        )
        self.graphql_name = name


def directive_field(name: str, *, default: Any = dataclasses.MISSING) -> Any:
    return DirectiveField(name, default=default)


def schema_directive(
    locations: Sequence[Location],
    *,
    name: str | None = None,
    description: str | None = None,
    repeatable: bool = False,
) -> Any:
    def wrap(cls: _type) -> _type:
        cls = dataclasses.dataclass(cls, kw_only=True)
        cls.__bramble_directive_info__ = describe_schema_directive(
            cls,
            locations=[location.value for location in locations],
            name=name,
            description=description,
            repeatable=repeatable,
        )
        return cls

    return wrap
