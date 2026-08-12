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

import abc
import inspect
from typing import TYPE_CHECKING, Any, ClassVar

from bramble._error import ErrorCode, GraphQLError

if TYPE_CHECKING:
    from bramble._resolver import Info


class BasePermission(abc.ABC):
    """Base class for a field permission check.

        class IsAuthenticated(bramble.BasePermission):
            message = "Not authenticated"

            def has_permission(self, source, info, **kwargs) -> bool:
                return info.context["user"] is not None

        @bramble.type
        class Query:
            @bramble.field(permission_classes=[IsAuthenticated])
            def secret() -> str:
                return "shh"

    Every listed class is instantiated and checked *before* the resolver runs, in declaration
    order, and the first failure short-circuits the rest -- so a cheap check can guard an
    expensive one. `has_permission` may be sync or `async def`.

    A denied field produces an ordinary GraphQL field error (`message`, or a generic default),
    which means it obeys the usual null-propagation rules: the field becomes `null`, bubbling to
    the nearest nullable ancestor. `error_extensions` is merged into the error's `extensions`.
    """

    #: Reported as the error message when this permission denies access.
    message: ClassVar[str | None] = None

    #: Extra keys merged into the resulting error's `extensions` object.
    error_extensions: ClassVar[dict[str, Any] | None] = None

    @abc.abstractmethod
    def has_permission(self, source: Any, info: "Info", **kwargs: Any) -> bool:
        """Return `True` to allow the field to resolve. May be `async def`.

        `source` is the parent value the field is being resolved on, and `kwargs` the field's own
        already-coerced GraphQL arguments.
        """
        raise NotImplementedError

    def on_denied(self) -> GraphQLError:
        """The error raised when this permission denies access -- override to customise the code
        or add structure beyond `message`/`error_extensions`.
        """
        return GraphQLError(
            self.message or "Permission denied",
            code=ErrorCode.FIELD_RESOLUTION_FAILED,
            extensions=dict(self.error_extensions or {}),
        )


async def check_permissions(
    permission_classes: tuple[type[BasePermission], ...],
    source: Any,
    info: "Info",
    arguments: dict[str, Any],
) -> None:
    """Runs each permission in declaration order, raising the first denial's own error.

    Instantiated per check rather than once per schema: a permission is free to hold per-request
    state, and sharing one instance across concurrent requests would make that unsafe.
    """
    for permission_class in permission_classes:
        permission = permission_class()
        allowed = permission.has_permission(source, info, **arguments)
        if inspect.isawaitable(allowed):
            allowed = await allowed
        if not allowed:
            raise permission.on_denied()


__all__ = ["BasePermission", "check_permissions"]
