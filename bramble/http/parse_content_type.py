from __future__ import annotations


def parse_content_type(value: str) -> tuple[str, dict[str, str]]:
    """Splits a `Content-Type` header into its bare MIME type and any `key=value` parameters
    (e.g. `multipart/form-data; boundary=----abc` -> `("multipart/form-data", {"boundary": "----abc"})`).
    Parameter values are only ever unquoted here (never otherwise decoded) -- the one parameter
    bramble's own HTTP layer actually reads (`boundary`) never needs anything more.
    """
    parts = value.split(";")
    mime_type = parts[0].strip().lower()

    params: dict[str, str] = {}
    for part in parts[1:]:
        if "=" not in part:
            continue
        key, _, raw_value = part.partition("=")
        params[key.strip().lower()] = raw_value.strip().strip('"')

    return mime_type, params
