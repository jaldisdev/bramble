#
# This source file is part of the Bramble open source project.
#
# Copyright (c) 2026 Jaldis B.V.
#
# Licensed under the MIT OR Apache-2.0 license (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://opensource.org/licenses/MIT
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

from __future__ import annotations

import bramble
import bramble.federation as federation

# Each federation directive applied in isolation, asserting the rendered SDL matches what the
# federation v2 spec itself declares -- de-risks Phase 2 (which composes several of these at once)
# by proving each one works standalone first.
#
# Two known, deliberate deviations from the spec's own literal declaration text (both inherited
# from bramble's pre-existing, already-documented SDL limitations, not new gaps):
#   - `@key`'s `resolvable: Boolean = true` renders as `resolvable: Boolean!` (no shown default) --
#     bramble's `ArgumentDefinition`/`DirectiveFieldDefinition` never render default *values*,
#     only whether one exists; Python-side application still works correctly regardless (see
#     `test_key_directive_defaults_resolvable_to_true` below).
#   - `@link`'s `import: [link__Import]` renders as `import: [String!]` -- this phase's own scope
#     limit (flat directive-name strings only, no `{name, as}` rename objects).


def _schema_for(directives: object, *, name: str = "Marked") -> bramble.Schema:
    directive_list = list(directives) if isinstance(directives, list) else [directives]

    @bramble.type(name=name, directives=directive_list)
    class Marked:
        id: str

    @bramble.type
    class Query:
        marked: Marked

    return bramble.Schema(query=Query, types=[Marked])


def test_key_directive_renders_repeatable_on_object_and_interface() -> None:
    schema = _schema_for(federation.Key(fields="id"))
    sdl = schema.to_sdl()

    assert 'type Marked @key(fields: "id", resolvable: true) {' in sdl
    assert "directive @key(fields: FieldSet!, resolvable: Boolean!) repeatable on OBJECT | INTERFACE" in sdl


def test_key_directive_defaults_resolvable_to_true() -> None:
    key = federation.Key(fields="id")
    assert key.resolvable is True


def test_key_directive_resolvable_false_renders() -> None:
    schema = _schema_for(federation.Key(fields="id", resolvable=False))
    assert 'resolvable: false' in schema.to_sdl()


def test_shareable_directive_is_repeatable_on_object_and_field_definition() -> None:
    schema = _schema_for(federation.Shareable())
    sdl = schema.to_sdl()

    assert "type Marked @shareable {" in sdl
    assert "directive @shareable repeatable on OBJECT | FIELD_DEFINITION" in sdl


def test_external_directive_renders() -> None:
    schema = _schema_for(federation.External())
    sdl = schema.to_sdl()

    assert "type Marked @external {" in sdl
    assert "directive @external on OBJECT | FIELD_DEFINITION" in sdl


def test_requires_and_provides_directives_render_on_field_definition() -> None:
    @bramble.type
    class Query:
        price: float = bramble.field(directives=[federation.Requires(fields="weight")], default=0.0)
        shipping: float = bramble.field(directives=[federation.Provides(fields="estimate")], default=0.0)

    schema = bramble.Schema(query=Query)
    sdl = schema.to_sdl()

    assert 'price: Float! @requires(fields: "weight")' in sdl
    assert 'shipping: Float! @provides(fields: "estimate")' in sdl
    assert "directive @requires(fields: FieldSet!) on FIELD_DEFINITION" in sdl
    assert "directive @provides(fields: FieldSet!) on FIELD_DEFINITION" in sdl


def test_override_directive_renders_from_and_optional_label() -> None:
    @bramble.type
    class Query:
        weight: float = bramble.field(directives=[federation.Override(from_="Inventory")], default=0.0)

    schema = bramble.Schema(query=Query)
    sdl = schema.to_sdl()

    # `label`'s unset default (`None`) still renders explicitly as `label: null` -- applied
    # directive fields have no "omit if equal to default" logic, matching how bramble already
    # treats every other directive field's value.
    assert 'weight: Float! @override(from: "Inventory", label: null)' in sdl
    assert "directive @override(from: String!, label: String) on FIELD_DEFINITION" in sdl


def test_inaccessible_directive_renders() -> None:
    schema = _schema_for(federation.Inaccessible())
    assert "type Marked @inaccessible {" in schema.to_sdl()


def test_tag_directive_is_repeatable_and_can_be_applied_multiple_times() -> None:
    schema = _schema_for([federation.Tag(name="internal"), federation.Tag(name="team-a")])
    sdl = schema.to_sdl()

    assert 'type Marked @tag(name: "internal") @tag(name: "team-a") {' in sdl
    assert "repeatable on" in sdl.split("directive @tag")[1].splitlines()[0]


def test_interface_object_directive_renders() -> None:
    schema = _schema_for(federation.InterfaceObject())
    assert "type Marked @interfaceObject {" in schema.to_sdl()


def test_compose_directive_renders_on_schema() -> None:
    @bramble.type
    class Query:
        greet: str

    schema = bramble.Schema(
        query=Query, schema_directives=[federation.ComposeDirective(name="@custom")]
    )
    sdl = schema.to_sdl()

    assert 'schema @composeDirective(name: "@custom") {' in sdl
    assert "directive @composeDirective(name: String!) repeatable on SCHEMA" in sdl


def test_authenticated_requires_scopes_and_policy_directives_render() -> None:
    schema = _schema_for(
        [
            federation.Authenticated(),
            federation.RequiresScopes(scopes=[["read"]]),
            federation.Policy(policies=[["admin"]]),
        ]
    )
    sdl = schema.to_sdl()

    assert "@authenticated" in sdl
    assert "@requiresScopes(scopes: [[\"read\"]])" in sdl
    assert '@policy(policies: [["admin"]])' in sdl


def test_link_directive_renders_url_and_import() -> None:
    @bramble.type
    class Query:
        greet: str

    schema = bramble.Schema(
        query=Query,
        schema_directives=[
            federation.Link(url="https://specs.apollo.dev/federation/v2.6", import_=["@key", "@shareable"])
        ],
    )
    sdl = schema.to_sdl()

    assert 'schema @link(url: "https://specs.apollo.dev/federation/v2.6", import: ["@key", "@shareable"]) {' in sdl
    assert "directive @link(url: String!, import: [String!]) repeatable on SCHEMA" in sdl


def test_link_directive_import_defaults_to_none() -> None:
    link = federation.Link(url="https://example.com")
    assert link.import_ is None
