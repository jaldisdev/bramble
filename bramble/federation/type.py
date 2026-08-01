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

"""`bramble.federation.type` -- sugar over `bramble.type(directives=[...])` for the handful of
federation directives commonly applied at the type level, building the equivalent `directives=`
list so callers don't have to import/spell out `Key`/`Shareable`/etc. themselves for the common
case. Kwarg names mirror the reference federation guide's own `federation.type(keys=[...])` shape.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from bramble._type import type as _type_decorator
from bramble.federation.directives import Inaccessible, InterfaceObject, Key, Shareable, Tag

_type = type  # capture the builtin before it's shadowed by a `type` parameter's annotation


def type(
    cls: _type | None = None,
    *,
    keys: Sequence[str] = (),
    shareable: bool = False,
    inaccessible: bool = False,
    tags: Sequence[str] = (),
    interface_object: bool = False,
    name: str | None = None,
    description: str | None = None,
    extra_directives: Sequence[object] = (),
) -> Callable[[_type], _type] | _type:
    directives: list[object] = [Key(fields=fields) for fields in keys]
    if shareable:
        directives.append(Shareable())
    if inaccessible:
        directives.append(Inaccessible())
    directives.extend(Tag(name=tag) for tag in tags)
    if interface_object:
        directives.append(InterfaceObject())
    directives.extend(extra_directives)

    def wrap(inner_cls: _type) -> _type:
        return _type_decorator(inner_cls, name=name, description=description, directives=directives)

    if cls is None:
        return wrap
    return wrap(cls)


__all__ = ["type"]
