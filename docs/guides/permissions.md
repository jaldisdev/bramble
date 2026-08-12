# Permissions

Guard a field with `bramble.BasePermission` subclasses, listed on the field:

```python
class IsAuthenticated(bramble.BasePermission):
    message = "Not authenticated"

    def has_permission(self, source, info, **kwargs) -> bool:
        return info.context["user"] is not None


@bramble.type
class Query:
    @bramble.field(permission_classes=[IsAuthenticated])
    def secret() -> str:
        return "shh"
```

Each class is instantiated and checked **before** the resolver runs, so a
denied field never executes its body. Checks run in declaration order and the
first failure short-circuits the rest, which lets a cheap check guard an
expensive one:

```python
@bramble.field(permission_classes=[IsAuthenticated, HasExpensiveEntitlement])
def report() -> Report: ...
```

`has_permission` receives the parent value as `source`, the execution `Info`,
and the field's own already-coerced GraphQL arguments as keyword arguments.
It may be `async def`.

## What a denial looks like

A denial produces an ordinary GraphQL field error, so it obeys the usual
null-propagation rules — the field becomes `null`, bubbling up to the nearest
nullable ancestor. A non-null field therefore nulls its parent:

```python
{"data": None,
 "errors": [{"message": "Not authenticated", "path": ["secret"],
             "extensions": {"code": "FIELD_RESOLUTION_FAILED", "reason": "auth"}}]}
```

Set `message` for the text and `error_extensions` for extra `extensions`
keys. Both are class-level:

```python
class IsAuthenticated(bramble.BasePermission):
    message = "Not authenticated"
    error_extensions: ClassVar[dict] = {"reason": "auth"}
```

Override `on_denied()` to return a fully custom `bramble.GraphQLError` —
a different `code`, say.

## Notes

- A permission is instantiated per check, not once per schema, so it may hold
  per-request state without that leaking across concurrent requests.
- Fields without a resolver are guarded too, not just resolver-backed ones.
- Permissions are execution-time only: they never appear in SDL or
  introspection, so a denied field is still *visible* in the schema. Hiding a
  field from the schema entirely is a different problem, and bramble has no
  built-in for it.
