"""Django integration -- an async HTTP view (`bramble.adapters.django.views.graphql_view`) plus,
if `channels` is installed (bramble's own `django` extra pulls it in), a Channels-based WebSocket
consumer in `bramble.adapters.django.channels` (kept as a separate submodule, not imported here, so
importing `bramble.adapters.django` itself never requires `channels` to be installed).
"""

from __future__ import annotations

from bramble.adapters.django.views import graphql_view

__all__ = ["graphql_view"]
