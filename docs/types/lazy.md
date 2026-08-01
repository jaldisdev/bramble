# Lazy types

`bramble.lazy(...)` breaks a circular import between two modules that need
to reference each other's types. A field annotated with a `bramble.lazy(...)`
forward reference doesn't import the referenced module until a `Schema()`
is actually built from it -- not at class-decoration time, which is exactly
when a circular top-level import would otherwise blow up.

```python
# authors.py
from __future__ import annotations
from typing import TYPE_CHECKING, Annotated
import bramble

if TYPE_CHECKING:
    from .posts import Post

@bramble.type
class Author:
    name: str
    posts: list[Annotated["Post", bramble.lazy(".posts")]]
```

```python
# posts.py
from __future__ import annotations
from typing import TYPE_CHECKING, Annotated
import bramble

if TYPE_CHECKING:
    from .authors import Author

@bramble.type
class Post:
    title: str
    author: Annotated["Author", bramble.lazy(".authors")]
```

Each module only needs the other's type under `TYPE_CHECKING` (for
type-checker support -- never actually imported at runtime by that guard)
plus a `bramble.lazy(...)`-tagged `Annotated[...]` wrapper around the
forward reference string. `lazy(module_path)` accepts a relative path
(starting with `.`, resolved against the calling module's own package, as
above) or an absolute one (`bramble.lazy("myapp.posts")`).

The referenced module is only imported once, lazily, the first time a
`Schema()` that reaches this field is constructed -- well after both
modules have finished loading, so there's no import-order to get right
between them.
