from __future__ import annotations

import pathlib

_GRAPHIQL_HTML_PATH = pathlib.Path(__file__).parent / "static" / "graphiql.html"


def get_graphql_ide_html() -> str:
    """The GraphiQL page served on a browser `GET` with no `query` parameter -- a small static
    HTML file loading GraphiQL's own UMD build from a CDN at browser runtime, not a vendored JS
    bundle bramble ships or builds itself.
    """
    return _GRAPHIQL_HTML_PATH.read_text(encoding="utf-8")
