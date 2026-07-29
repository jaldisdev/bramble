from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

import bramble

if TYPE_CHECKING:
    from .authors import Author


@bramble.type
class Post:
    title: str
    author: Annotated["Author", bramble.lazy(".authors")]
