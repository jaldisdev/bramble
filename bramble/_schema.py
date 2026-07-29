from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from bramble.schema.config import SchemaConfig

_type = type  # capture the builtin before it's shadowed by a `type` parameter's annotation


class Schema:
    def __init__(
        self,
        query: _type,
        mutation: _type | None = None,
        subscription: _type | None = None,
        directives: Sequence[Callable[..., Any]] = (),
        types: Sequence[_type] = (),
        extensions: Sequence[object] = (),
        config: SchemaConfig | None = None,
        execution_context_class: _type | None = None,
    ) -> None:
        # Stub: stores the schema definition as given. It does not yet walk the type graph,
        # cross-validate it (interface field contracts, directive locations), or compile it
        # into an executable schema IR -- that lands once the rest of the pipeline (query
        # validation, execution bridge) exists for it to feed into.
        self.query = query
        self.mutation = mutation
        self.subscription = subscription
        self.directives = tuple(directives)
        self.types = tuple(types)
        self.extensions = tuple(extensions)
        self.config = config if config is not None else SchemaConfig()
        self.execution_context_class = execution_context_class
