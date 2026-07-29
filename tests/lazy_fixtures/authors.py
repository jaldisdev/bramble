from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

import bramble

if TYPE_CHECKING:
    from .posts import Post


@bramble.input
class AuthorSearch:
    name: str


@bramble.type
class Author:
    name: str
    posts: list[Annotated["Post", bramble.lazy(".posts")]]
