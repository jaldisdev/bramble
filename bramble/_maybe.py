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

from typing import TYPE_CHECKING, Generic, TypeVar

ValueType = TypeVar("ValueType")


class Some(Generic[ValueType]):
    """A present value, including a present `None`.

    What a `Maybe[T]` field holds once the client actually supplied something. Truthy even when it
    wraps `None`, so `if field:` means "was it provided", and `field.value` is what was provided.
    """

    __slots__ = ("value",)

    def __init__(self, value: ValueType) -> None:
        self.value = value

    def __repr__(self) -> str:
        return f"Some({self.value!r})"

    def __eq__(self, other: object) -> bool:
        return self.value == other.value if isinstance(other, Some) else NotImplemented

    def __hash__(self) -> int:
        return hash(self.value)

    def __bool__(self) -> bool:
        return True


if TYPE_CHECKING:
    from typing import TypeAlias

    Maybe: TypeAlias = Some[ValueType] | None
else:

    class Maybe(Generic[ValueType]):
        """Runtime marker. Declared as a real class rather than a type alias so bramble can
        recognise `Maybe[T]` by `typing.get_origin`; a type checker sees the alias above instead.
        """


__all__ = ["Maybe", "Some"]
