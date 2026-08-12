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

from typing import Any


class UnsetType:
    """The type of `bramble.UNSET`. A singleton, so `value is bramble.UNSET` is the check to use.

    Falsy and empty-stringing deliberately, matching how an absent value reads in a boolean or
    string context.
    """

    _instance: "UnsetType | None" = None

    def __new__(cls) -> "UnsetType":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __str__(self) -> str:
        return ""

    def __repr__(self) -> str:
        return "UNSET"

    def __bool__(self) -> bool:
        return False


UNSET: Any = UnsetType()
"""A sentinel distinguishing "no value was provided" from "null was provided explicitly".

GraphQL treats those as different things, but Python's `None` collapses them. Use `UNSET` as the
default for a nullable argument or input field when that difference matters -- a partial-update
mutation being the usual case, where "leave this alone" and "clear this" must not be confused:

    @bramble.input
    class UpdateUser:
        name: str | None = bramble.UNSET
        nickname: str | None = bramble.UNSET

    @bramble.mutation
    def update_user(input: UpdateUser) -> User:
        if input.nickname is not bramble.UNSET:
            # Explicitly provided -- possibly as null, meaning "clear it".
            user.nickname = input.nickname
        return user

An argument defaulting to `UNSET` is optional in the schema, and no default is rendered for it in
SDL: `UNSET` has no GraphQL literal spelling, and printing one would claim a default the server
doesn't actually apply.
"""

__all__ = ["UNSET", "UnsetType"]
