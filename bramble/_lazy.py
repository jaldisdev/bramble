from __future__ import annotations

import ast
import dataclasses
import importlib
import importlib.util
import sys
import typing
from collections.abc import Callable
from typing import Any, ForwardRef


@dataclasses.dataclass(frozen=True)
class LazyType:
    """A placeholder standing in for a type that lives in another module (§ circular imports):
    produced wherever a `bramble.lazy(...)`-tagged forward reference gets evaluated, and only
    turned into the real class later, once it's safe to import that module (`resolve_type`).
    """

    type_name: str
    module: str
    package: str | None = None

    def resolve_type(self) -> type:
        module = importlib.import_module(self.module, self.package)

        if self.package:
            full_module_name = importlib.util.resolve_name(self.module, self.package)
        else:
            full_module_name = self.module

        # A lazy reference pointing at the entrypoint script itself resolves to a *different*
        # module object than `sys.modules["__main__"]` (the same file imported under its real
        # name vs. as `__main__`) -- normalizing to the `__main__` module avoids registering the
        # same class twice under two different module identities.
        main_module = sys.modules.get("__main__")
        if main_module is not None and getattr(main_module, "__spec__", None) is not None:
            if main_module.__spec__.name == full_module_name:
                module = main_module

        return getattr(module, self.type_name)


class LazyReference:
    """Returned by `bramble.lazy(...)`. Not meant to be constructed directly."""

    def __init__(self, module: str) -> None:
        self.module = module
        self.package: str | None = None

        if module.startswith("."):
            # Frame 0 is this __init__, frame 1 is `lazy()`'s own frame, frame 2 is `lazy()`'s
            # caller -- the user's module where `bramble.lazy(".authors")` was actually written.
            # A relative module path is only meaningful resolved against *that* module's package.
            frame = sys._getframe(2)
            self.package = frame.f_globals["__package__"]

    def resolve_forward_ref(self, forward_ref: ForwardRef) -> LazyType:
        return LazyType(forward_ref.__forward_arg__, self.module, self.package)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, LazyReference):
            return NotImplemented
        return self.module == other.module and self.package == other.package

    def __hash__(self) -> int:
        return hash((self.__class__, self.module, self.package))


def lazy(module_path: str) -> LazyReference:
    """Creates a lazy reference to a type in another module, for use inside `Annotated[...]`:

    ```python
    if TYPE_CHECKING:
        from .authors import Author

    @bramble.type
    class Post:
        author: Annotated["Author", bramble.lazy(".authors")]
    ```

    `module_path` supports relative paths starting with `.`, resolved against the package of
    whichever module calls `lazy()`. The referenced module is only actually imported once a
    `Schema()` is built from a type using it, not at class-decoration time -- this is what lets
    two modules reference each other's types without a circular top-level import.
    """
    return LazyReference(module_path)


def _lazy_reference_marker(metadata: list[Any]) -> LazyReference | None:
    for item in metadata:
        if isinstance(item, LazyReference):
            return item
    return None


def _namespace_from_live_annotation(annotation: Any) -> dict[str, Any]:
    """Walks an already-evaluated annotation object (no `from __future__ import annotations` in
    play, or an annotation that was never a string to begin with) looking for
    `Annotated[ForwardRef(...), bramble.lazy(...)]`. Safe to introspect directly -- no `eval()`
    involved anywhere in this path.
    """
    namespace: dict[str, Any] = {}
    origin = typing.get_origin(annotation)

    if origin is typing.Annotated:
        underlying, *metadata = typing.get_args(annotation)
        lazy_reference = _lazy_reference_marker(metadata)
        if lazy_reference is not None and isinstance(underlying, ForwardRef):
            lazy_type = lazy_reference.resolve_forward_ref(underlying)
            namespace[lazy_type.type_name] = lazy_type
            return namespace
        namespace.update(_namespace_from_live_annotation(underlying))
        return namespace

    if origin is not None:
        for member in typing.get_args(annotation):
            namespace.update(_namespace_from_live_annotation(member))

    return namespace


def _namespace_from_source(raw: str, globalns: dict[str, Any], localns: dict[str, Any]) -> dict[str, Any]:
    """Walks the AST of a still-deferred (`from __future__ import annotations`) annotation
    string, looking for `Annotated[...]` subscripts, without ever `eval()`-ing the whole string --
    the forward-ref target itself (e.g. `"Author"`) is by definition not yet resolvable, so
    evaluating the full expression would raise `NameError` before we could inspect anything.
    """
    namespace: dict[str, Any] = {}
    try:
        tree = ast.parse(raw, mode="eval")
    except SyntaxError:
        return namespace

    def visit(node: ast.expr) -> None:
        # `X | None` (PEP 604) parses as a BinOp, not a Subscript -- recurse into both operands
        # the same way a Subscript's own elements get walked below.
        if isinstance(node, ast.BinOp):
            visit(node.left)
            visit(node.right)
            return

        if not isinstance(node, ast.Subscript):
            return

        target_name = None
        if isinstance(node.value, ast.Name):
            target_name = node.value.id
        elif isinstance(node.value, ast.Attribute):
            target_name = node.value.attr

        elements = node.slice.elts if isinstance(node.slice, ast.Tuple) else [node.slice]

        if target_name == "Annotated" and elements:
            forward_ref_node, *metadata_nodes = elements
            if isinstance(forward_ref_node, ast.Constant) and isinstance(forward_ref_node.value, str):
                type_name = forward_ref_node.value
                for metadata_node in metadata_nodes:
                    try:
                        evaluated = eval(ast.unparse(metadata_node), globalns, localns)  # noqa: S307
                    except NameError:
                        continue
                    if isinstance(evaluated, LazyReference):
                        lazy_type = evaluated.resolve_forward_ref(ForwardRef(type_name))
                        namespace[lazy_type.type_name] = lazy_type

        for element in elements:
            visit(element)

    visit(tree.body)
    return namespace


def _namespace_from_raw_annotation(
    raw: Any, globalns: dict[str, Any], localns: dict[str, Any]
) -> dict[str, Any]:
    if isinstance(raw, str):
        return _namespace_from_source(raw, globalns, localns)
    return _namespace_from_live_annotation(raw)


def namespace_for_class(cls: type) -> dict[str, Any]:
    """Collects a `{forward_ref_name: LazyType(...)}` entry for every `bramble.lazy()`-tagged
    annotation reachable from `cls`'s own dataclass fields (walking `cls.__mro__` base-to-derived,
    matching `typing.get_type_hints`'s own resolution order so a subclass's re-annotation of an
    inherited field wins). Merge the result into whatever `localns` a `get_type_hints(cls, ...)`
    call is about to use -- without it, `get_type_hints` raises `NameError` on the forward-ref
    name before ever getting a chance to notice the `lazy()` marker.
    """
    namespace: dict[str, Any] = {}
    for base in reversed(cls.__mro__):
        if base is object:
            continue
        raw_annotations = base.__dict__.get("__annotations__", {})
        if not raw_annotations:
            continue
        module = sys.modules.get(getattr(base, "__module__", None))
        globalns = vars(module) if module is not None else {}
        for raw in raw_annotations.values():
            namespace.update(_namespace_from_raw_annotation(raw, globalns, namespace))
    return namespace


def namespace_for_callable(func: Callable[..., Any]) -> dict[str, Any]:
    """Same as `namespace_for_class`, for a single function's own parameter/return annotations
    (a resolver method or a standalone operation-directive function).
    """
    raw_annotations = getattr(func, "__annotations__", {})
    module = sys.modules.get(getattr(func, "__module__", None))
    globalns = vars(module) if module is not None else {}
    namespace: dict[str, Any] = {}
    for raw in raw_annotations.values():
        namespace.update(_namespace_from_raw_annotation(raw, globalns, namespace))
    return namespace
