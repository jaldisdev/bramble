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

from bramble.http.async_base_view import AsyncBaseHTTPView
from bramble.http.base import BaseRequestProtocol, BaseView
from bramble.http.exceptions import HTTPException
from bramble.http.types import GraphQLRequestData, HTTPMethod, QueryParams

# `AsyncBaseHTTPView`, `BaseView` and `BaseRequestProtocol` are the contract a framework adapter
# subclasses or satisfies, so they are exported rather than left to a deep import -- matching
# `bramble.subscriptions`, which already exposes `GraphQLTransportWSHandler` as the hook for a
# custom transport. Supporting a framework outside the shipped five is meant to be an extension
# point, not a fork.
__all__ = [
    "AsyncBaseHTTPView",
    "BaseRequestProtocol",
    "BaseView",
    "GraphQLRequestData",
    "HTTPException",
    "HTTPMethod",
    "QueryParams",
]
