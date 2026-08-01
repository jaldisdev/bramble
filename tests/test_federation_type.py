from __future__ import annotations

import bramble
import bramble.federation as federation
from bramble.schema_directive import Location

# `federation.type(keys=[...])` is sugar over `bramble.type(directives=[federation.Key(...), ...])`
# -- these tests assert the sugar form renders byte-identical SDL to the equivalent manual form.


def test_keys_sugar_matches_manual_key_directive() -> None:
    @federation.type(keys=["id"])
    class SugarProduct:
        id: str

    @bramble.type(directives=[federation.Key(fields="id")])
    class ManualProduct:
        id: str

    @bramble.type
    class Query:
        sugar: SugarProduct
        manual: ManualProduct

    schema = bramble.Schema(query=Query, types=[SugarProduct, ManualProduct])
    sdl = schema.to_sdl()

    assert 'type SugarProduct @key(fields: "id", resolvable: true) {' in sdl
    assert 'type ManualProduct @key(fields: "id", resolvable: true) {' in sdl


def test_multiple_keys_shareable_inaccessible_tags_and_interface_object() -> None:
    @federation.type(
        keys=["id", "sku"],
        shareable=True,
        inaccessible=True,
        tags=["internal", "team-a"],
        interface_object=True,
    )
    class Widget:
        id: str
        sku: str

    @bramble.type
    class Query:
        widget: Widget

    schema = bramble.Schema(query=Query, types=[Widget])
    sdl = schema.to_sdl()

    assert (
        'type Widget @key(fields: "id", resolvable: true) @key(fields: "sku", resolvable: true) @shareable @inaccessible '
        '@tag(name: "internal") @tag(name: "team-a") @interfaceObject {' in sdl
    )


def test_bare_decorator_form_works_without_any_federation_options() -> None:
    @federation.type
    class Plain:
        id: str

    assert Plain.__bramble_type_info__.name == "Plain"


def test_name_description_and_extra_directives_are_forwarded() -> None:
    @bramble.schema_directive(locations=[Location.OBJECT])
    class Custom:
        pass

    @federation.type(keys=["id"], name="Renamed", description="A renamed type", extra_directives=[Custom()])
    class Original:
        id: str

    @bramble.type
    class Query:
        original: Original

    schema = bramble.Schema(query=Query, types=[Original])
    sdl = schema.to_sdl()

    assert '"""A renamed type"""' in sdl
    assert 'type Renamed @key(fields: "id", resolvable: true) @custom {' in sdl
