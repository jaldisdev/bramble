//
// This source file is part of the Bramble open source project.
//
// Copyright (c) 2026 Jaldis B.V.
//
// Licensed under the MIT OR Apache-2.0 license (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     https://opensource.org/licenses/MIT
//     https://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.
//

use crate::naming::to_camel_case;
use crate::schema::{
    AppliedDirective, ArgumentDefinition, CompiledSchema, DirectiveFieldDefinition, EnumValueDefinition, FieldDefinition,
    OperationDirectiveDefinition, OperationDirectiveLocation, SchemaDirectiveDefinition, SchemaDirectiveLocation,
    TypeDefinition, TypeKind, UnionDefinition,
};

/// The GraphQL-facing name for a field/argument/directive-field with no explicit `name=`
/// override -- mirrors `bramble_core::validation`'s own `field_key`/`argument_key` exactly, so
/// SDL rendering shows the same name the query executor actually looks fields/arguments up by.
fn effective_name(name: &str, graphql_name: &Option<String>, auto_camel_case: bool) -> String {
    if let Some(graphql_name) = graphql_name {
        return graphql_name.clone();
    }
    if auto_camel_case {
        to_camel_case(name)
    } else {
        name.to_string()
    }
}

fn render_description(description: &Option<String>, indent: &str) -> String {
    match description {
        Some(text) if !text.is_empty() => format!("{indent}\"\"\"{text}\"\"\"\n"),
        _ => String::new(),
    }
}

fn render_json_value(value: &serde_json::Value) -> String {
    match value {
        serde_json::Value::Null => "null".to_string(),
        serde_json::Value::Bool(flag) => flag.to_string(),
        serde_json::Value::Number(number) => number.to_string(),
        serde_json::Value::String(text) => format!("{text:?}"),
        serde_json::Value::Array(items) => {
            let rendered: Vec<String> = items.iter().map(render_json_value).collect();
            format!("[{}]", rendered.join(", "))
        }
        serde_json::Value::Object(map) => {
            let rendered: Vec<String> = map
                .iter()
                .map(|(key, value)| format!("{key}: {}", render_json_value(value)))
                .collect();
            format!("{{{}}}", rendered.join(", "))
        }
    }
}

fn render_applied_directive(directive: &AppliedDirective) -> String {
    if directive.arguments.is_empty() {
        return format!("@{}", directive.name);
    }
    let rendered: Vec<String> = directive
        .arguments
        .iter()
        .map(|(name, value)| format!("{name}: {}", render_json_value(value)))
        .collect();
    format!("@{}({})", directive.name, rendered.join(", "))
}

fn render_applied_directives(directives: &[AppliedDirective]) -> String {
    directives
        .iter()
        .map(|directive| format!(" {}", render_applied_directive(directive)))
        .collect()
}

fn render_argument(argument: &ArgumentDefinition, auto_camel_case: bool) -> String {
    let name = effective_name(&argument.name, &argument.graphql_name, auto_camel_case);
    // An argument's description renders inline (`"""text""" name: Type`) rather than on its own
    // line the way a field's does: arguments are printed comma-separated inside one `(...)`, so a
    // line break here would split the argument list across lines mid-expression.
    let description = match &argument.description {
        Some(text) if !text.is_empty() => format!("\"\"\"{text}\"\"\" "),
        _ => String::new(),
    };
    let mut out = format!("{description}{name}: {}", argument.graphql_type.to_sdl_string());
    // `= <default>` goes between the type and any directives, per the spec's `InputValueDefinition`
    // grammar. Without it a defaulted non-null argument reads as *required* to every schema
    // consumer, even though bramble's own validation treats it as optional.
    if let Some(default) = &argument.default_value {
        out.push_str(&format!(" = {default}"));
    }
    if let Some(reason) = &argument.deprecation_reason {
        out.push_str(&format!(" @deprecated(reason: {reason:?})"));
    }
    out.push_str(&render_applied_directives(&argument.applied_directives));
    out
}

fn render_arguments(arguments: &[ArgumentDefinition], auto_camel_case: bool) -> String {
    if arguments.is_empty() {
        return String::new();
    }
    let rendered: Vec<String> = arguments
        .iter()
        .map(|argument| render_argument(argument, auto_camel_case))
        .collect();
    format!("({})", rendered.join(", "))
}

/// Whether a type/field name is reserved for introspection (§ "Reserved Names": everything
/// beginning with `__`). These are implicit in every schema, so SDL never declares them -- printing
/// `__Schema`/`__type` would make the output non-portable, since another server reading it back
/// would reject redefining reserved names.
fn is_reserved_name(name: &str) -> bool {
    name.starts_with("__")
}

fn render_field(field: &FieldDefinition, auto_camel_case: bool, is_input: bool) -> String {
    let mut out = String::new();
    out.push_str(&render_description(&field.description, "  "));
    out.push_str("  ");
    out.push_str(&effective_name(&field.name, &field.graphql_name, auto_camel_case));
    out.push_str(&render_arguments(&field.arguments, auto_camel_case));
    out.push_str(": ");
    out.push_str(&field.graphql_type.to_sdl_string());
    // Gated on `is_input`: GraphQL's `FieldDefinition` grammar has no default at all, only
    // `InputValueDefinition` does. Rendering one on an object field would produce invalid SDL.
    if is_input && let Some(default) = &field.default_value {
        out.push_str(&format!(" = {default}"));
    }
    if let Some(reason) = &field.deprecation_reason {
        out.push_str(&format!(" @deprecated(reason: {reason:?})"));
    }
    out.push_str(&render_applied_directives(&field.applied_directives));
    out.push('\n');
    out
}

fn kind_keyword(kind: TypeKind) -> &'static str {
    match kind {
        TypeKind::Type => "type",
        TypeKind::Interface => "interface",
        TypeKind::Input => "input",
        TypeKind::Enum => "enum",
    }
}

/// One enum member: its (possibly overridden) GraphQL name, plus `@deprecated`/applied directives.
/// Deliberately *not* run through `effective_name`'s camelCase conversion the way a field or
/// argument is -- GraphQL enum members are conventionally SCREAMING_SNAKE_CASE, which is already
/// how they're spelled in Python, so camelCasing `RED`/`IN_PROGRESS` would corrupt every name.
fn render_enum_value(value: &EnumValueDefinition) -> String {
    let mut out = render_description(&value.description, "  ");
    out.push_str("  ");
    out.push_str(value.graphql_name.as_ref().unwrap_or(&value.name));
    if let Some(reason) = &value.deprecation_reason {
        out.push_str(&format!(" @deprecated(reason: {reason:?})"));
    }
    out.push_str(&render_applied_directives(&value.applied_directives));
    out.push('\n');
    out
}

fn render_type(type_def: &TypeDefinition, auto_camel_case: bool) -> String {
    let mut out = String::new();
    out.push_str(&render_description(&type_def.description, ""));
    out.push_str(kind_keyword(type_def.kind));
    out.push(' ');
    out.push_str(&type_def.name);
    if !type_def.interfaces.is_empty() {
        out.push_str(" implements ");
        out.push_str(&type_def.interfaces.join(" & "));
    }
    out.push_str(&render_applied_directives(&type_def.applied_directives));
    if type_def.kind == TypeKind::Input && type_def.one_of {
        out.push_str(" @oneOf");
    }
    out.push_str(" {\n");
    if type_def.kind == TypeKind::Enum {
        for value in &type_def.enum_values {
            out.push_str(&render_enum_value(value));
        }
    } else {
        for field in &type_def.fields {
            if is_reserved_name(&effective_name(&field.name, &field.graphql_name, auto_camel_case)) {
                continue;
            }
            out.push_str(&render_field(field, auto_camel_case, type_def.kind == TypeKind::Input));
        }
    }
    out.push('}');
    out
}

fn render_union(union_def: &UnionDefinition) -> String {
    format!("union {} = {}", union_def.name, union_def.member_names.join(" | "))
}

fn render_scalar(name: &str, description: Option<&String>, applied_directives: &[AppliedDirective]) -> String {
    let description = description.cloned();
    let mut out = render_description(&description, "");
    out.push_str(&format!("scalar {name}{}", render_applied_directives(applied_directives)));
    out
}

fn operation_directive_location_str(location: OperationDirectiveLocation) -> &'static str {
    match location {
        OperationDirectiveLocation::Query => "QUERY",
        OperationDirectiveLocation::Mutation => "MUTATION",
        OperationDirectiveLocation::Subscription => "SUBSCRIPTION",
        OperationDirectiveLocation::Field => "FIELD",
        OperationDirectiveLocation::FragmentDefinition => "FRAGMENT_DEFINITION",
        OperationDirectiveLocation::FragmentSpread => "FRAGMENT_SPREAD",
        OperationDirectiveLocation::InlineFragment => "INLINE_FRAGMENT",
    }
}

fn render_operation_directive(directive: &OperationDirectiveDefinition, auto_camel_case: bool) -> String {
    let mut out = String::new();
    out.push_str(&render_description(&directive.description, ""));
    out.push_str(&format!("directive @{}", directive.name));
    out.push_str(&render_arguments(&directive.arguments, auto_camel_case));
    out.push_str(" on ");
    let locations: Vec<&str> = directive
        .locations
        .iter()
        .copied()
        .map(operation_directive_location_str)
        .collect();
    out.push_str(&locations.join(" | "));
    out
}

fn schema_directive_location_str(location: SchemaDirectiveLocation) -> &'static str {
    match location {
        SchemaDirectiveLocation::Schema => "SCHEMA",
        SchemaDirectiveLocation::Scalar => "SCALAR",
        SchemaDirectiveLocation::Object => "OBJECT",
        SchemaDirectiveLocation::FieldDefinition => "FIELD_DEFINITION",
        SchemaDirectiveLocation::ArgumentDefinition => "ARGUMENT_DEFINITION",
        SchemaDirectiveLocation::Interface => "INTERFACE",
        SchemaDirectiveLocation::Union => "UNION",
        SchemaDirectiveLocation::Enum => "ENUM",
        SchemaDirectiveLocation::EnumValue => "ENUM_VALUE",
        SchemaDirectiveLocation::InputObject => "INPUT_OBJECT",
        SchemaDirectiveLocation::InputFieldDefinition => "INPUT_FIELD_DEFINITION",
    }
}

fn render_directive_field(field: &DirectiveFieldDefinition, auto_camel_case: bool) -> String {
    let name = effective_name(&field.name, &field.graphql_name, auto_camel_case);
    format!("{name}: {}", field.graphql_type.to_sdl_string())
}

fn render_schema_directive(directive: &SchemaDirectiveDefinition, auto_camel_case: bool) -> String {
    let mut out = String::new();
    out.push_str(&render_description(&directive.description, ""));
    out.push_str(&format!("directive @{}", directive.name));
    if !directive.fields.is_empty() {
        let rendered: Vec<String> = directive
            .fields
            .iter()
            .map(|field| render_directive_field(field, auto_camel_case))
            .collect();
        out.push_str(&format!("({})", rendered.join(", ")));
    }
    if directive.repeatable {
        out.push_str(" repeatable");
    }
    out.push_str(" on ");
    let locations: Vec<&str> = directive
        .locations
        .iter()
        .copied()
        .map(schema_directive_location_str)
        .collect();
    out.push_str(&locations.join(" | "));
    out
}

fn render_schema_block(schema: &CompiledSchema) -> String {
    let mut out = String::from("schema");
    out.push_str(&render_applied_directives(&schema.schema_applied_directives));
    out.push_str(" {\n");
    out.push_str(&format!("  query: {}\n", schema.query_type_name));
    if let Some(mutation) = &schema.mutation_type_name {
        out.push_str(&format!("  mutation: {mutation}\n"));
    }
    if let Some(subscription) = &schema.subscription_type_name {
        out.push_str(&format!("  subscription: {subscription}\n"));
    }
    out.push('}');
    out
}

/// Renders `schema` as GraphQL SDL (§6/§9/§12). Declarations within each kind (types, unions,
/// scalars, operation directives, schema directives) are sorted alphabetically by name -- the
/// only ordering `CompiledSchema`'s `HashMap`s actually give is iteration order, which isn't
/// stable across runs, and snapshot testing needs reproducible output. A field's own declaration
/// order within its type *is* preserved (`Vec`, populated straight off `dataclasses.fields()`).
///
/// Known, deliberately flagged gaps (not silently wrong): an argument default with no faithful
/// GraphQL literal spelling (an arbitrary Python object) renders no `= <default>` at all, since a
/// wrong literal would be worse than an absent one; `@skip`/`@include` never appear (they're always
/// built into every GraphQL service, never registered as a custom operation directive to begin
/// with); an *input object field's* own default never renders, because `FieldDefinition` -- unlike
/// `ArgumentDefinition` -- carries no default at all.
pub fn render_sdl(schema: &CompiledSchema) -> String {
    let mut sections = vec![render_schema_block(schema)];

    let mut type_names: Vec<&String> = schema.types.keys().filter(|name| !is_reserved_name(name)).collect();
    type_names.sort();
    for name in type_names {
        sections.push(render_type(&schema.types[name], schema.auto_camel_case));
    }

    let mut union_names: Vec<&String> = schema.unions.keys().collect();
    union_names.sort();
    for name in union_names {
        sections.push(render_union(&schema.unions[name]));
    }

    let mut scalar_names: Vec<&String> = schema.scalar_names.iter().collect();
    scalar_names.sort();
    for name in scalar_names {
        let applied_directives = schema.scalar_applied_directives.get(name).map(Vec::as_slice).unwrap_or(&[]);
        let description = schema.scalar_descriptions.get(name);
        sections.push(render_scalar(name, description, applied_directives));
    }

    let mut operation_directive_names: Vec<&String> = schema.operation_directives.keys().collect();
    operation_directive_names.sort();
    for name in operation_directive_names {
        sections.push(render_operation_directive(
            &schema.operation_directives[name],
            schema.auto_camel_case,
        ));
    }

    let mut schema_directive_names: Vec<&String> = schema.schema_directives.keys().collect();
    schema_directive_names.sort();
    for name in schema_directive_names {
        sections.push(render_schema_directive(
            &schema.schema_directives[name],
            schema.auto_camel_case,
        ));
    }

    sections.join("\n\n")
}

#[cfg(test)]
mod tests {
    use std::collections::{HashMap, HashSet};

    use super::*;
    use crate::persisted_query::PersistedQueryCache;
    use crate::schema::GraphQLType;

    fn empty_schema() -> CompiledSchema {
        CompiledSchema {
            types: HashMap::new(),
            unions: HashMap::new(),
            query_type_name: "Query".to_string(),
            mutation_type_name: None,
            subscription_type_name: None,
            operation_directives: HashMap::new(),
            schema_directives: HashMap::new(),
            schema_applied_directives: Vec::new(),
            scalar_names: HashSet::new(),
            scalar_applied_directives: HashMap::new(),
            scalar_descriptions: HashMap::new(),
            auto_camel_case: true,
            persisted_query_cache: PersistedQueryCache::new(),
        }
    }

    #[test]
    fn renders_a_simple_object_type() {
        let mut schema = empty_schema();
        schema.types.insert(
            "Query".to_string(),
            TypeDefinition {
                kind: TypeKind::Type,
                name: "Query".to_string(),
                description: Some("The root query type".to_string()),
                one_of: false,
                interfaces: Vec::new(),
                enum_values: Vec::new(),
                fields: vec![FieldDefinition {
                    name: "greet".to_string(),
                    graphql_name: None,
                    graphql_type: GraphQLType::NonNull(Box::new(GraphQLType::Named("String".to_string()))),
                    description: None,
                    deprecation_reason: None,
                    default_value: None,
                    is_maybe: false,
                    has_resolver: true,
                    parent_parameter: None,
                    info_parameter: None,
                    arguments: vec![ArgumentDefinition {
                        name: "name".to_string(),
                        graphql_name: None,
                        graphql_type: GraphQLType::NonNull(Box::new(GraphQLType::Named("String".to_string()))),
                        has_default: false,
                        default_value: None,
                        is_maybe: false,
                        description: None,
                        deprecation_reason: None,
                        applied_directives: Vec::new(),
                    }],
                    applied_directives: Vec::new(),
                }],
                applied_directives: Vec::new(),
            },
        );

        let sdl = render_sdl(&schema);
        insta::assert_snapshot!(sdl, @r###"
        schema {
          query: Query
        }

        """The root query type"""
        type Query {
          greet(name: String!): String!
        }
        "###);
    }

    #[test]
    fn renders_applied_directives_with_argument_values() {
        let mut schema = empty_schema();
        schema.types.insert(
            "User".to_string(),
            TypeDefinition {
                kind: TypeKind::Type,
                name: "User".to_string(),
                description: None,
                one_of: false,
                interfaces: Vec::new(),
                enum_values: Vec::new(),
                fields: Vec::new(),
                applied_directives: vec![AppliedDirective {
                    name: "keys".to_string(),
                    arguments: vec![("fields".to_string(), serde_json::json!("id"))],
                }],
            },
        );

        let sdl = render_sdl(&schema);
        assert!(sdl.contains("type User @keys(fields: \"id\") {\n}"));
    }

    #[test]
    fn renders_union_and_scalar_declarations() {
        let mut schema = empty_schema();
        schema.unions.insert(
            "MediaItem".to_string(),
            UnionDefinition {
                name: "MediaItem".to_string(),
                description: None,
                member_names: vec!["Audio".to_string(), "Video".to_string()],
                has_custom_resolve_type: false,
            },
        );
        schema.scalar_names.insert("Base64".to_string());

        let sdl = render_sdl(&schema);
        assert!(sdl.contains("union MediaItem = Audio | Video"));
        assert!(sdl.contains("scalar Base64"));
    }

    #[test]
    fn renders_an_interface_and_input_with_one_of() {
        let mut schema = empty_schema();
        schema.types.insert(
            "Node".to_string(),
            TypeDefinition {
                kind: TypeKind::Interface,
                name: "Node".to_string(),
                description: None,
                one_of: false,
                interfaces: Vec::new(),
                enum_values: Vec::new(),
                fields: vec![FieldDefinition {
                    name: "id".to_string(),
                    graphql_name: None,
                    graphql_type: GraphQLType::NonNull(Box::new(GraphQLType::Named("ID".to_string()))),
                    description: None,
                    deprecation_reason: None,
                    default_value: None,
                    is_maybe: false,
                    has_resolver: false,
                    parent_parameter: None,
                    info_parameter: None,
                    arguments: Vec::new(),
                    applied_directives: Vec::new(),
                }],
                applied_directives: Vec::new(),
            },
        );
        schema.types.insert(
            "UserFilter".to_string(),
            TypeDefinition {
                kind: TypeKind::Input,
                name: "UserFilter".to_string(),
                description: None,
                one_of: true,
                interfaces: Vec::new(),
                enum_values: Vec::new(),
                fields: vec![
                    FieldDefinition {
                        name: "by_id".to_string(),
                        graphql_name: Some("byId".to_string()),
                        graphql_type: GraphQLType::Named("ID".to_string()),
                        description: None,
                        deprecation_reason: None,
                        default_value: None,
                        is_maybe: false,
                        has_resolver: false,
                        parent_parameter: None,
                        info_parameter: None,
                        arguments: Vec::new(),
                        applied_directives: Vec::new(),
                    },
                    FieldDefinition {
                        name: "by_name".to_string(),
                        graphql_name: Some("byName".to_string()),
                        graphql_type: GraphQLType::Named("String".to_string()),
                        description: None,
                        deprecation_reason: None,
                        default_value: None,
                        is_maybe: false,
                        has_resolver: false,
                        parent_parameter: None,
                        info_parameter: None,
                        arguments: Vec::new(),
                        applied_directives: Vec::new(),
                    },
                ],
                applied_directives: Vec::new(),
            },
        );

        let sdl = render_sdl(&schema);
        insta::assert_snapshot!(sdl, @r###"
        schema {
          query: Query
        }

        interface Node {
          id: ID!
        }

        input UserFilter @oneOf {
          byId: ID
          byName: String
        }
        "###);
    }

    #[test]
    fn renders_a_field_deprecated_via_an_argument() {
        let mut schema = empty_schema();
        schema.types.insert(
            "Query".to_string(),
            TypeDefinition {
                kind: TypeKind::Type,
                name: "Query".to_string(),
                description: None,
                one_of: false,
                interfaces: Vec::new(),
                enum_values: Vec::new(),
                fields: vec![FieldDefinition {
                    name: "old_field".to_string(),
                    graphql_name: Some("oldField".to_string()),
                    graphql_type: GraphQLType::NonNull(Box::new(GraphQLType::Named("String".to_string()))),
                    description: None,
                    deprecation_reason: None,
                    default_value: None,
                    is_maybe: false,
                    has_resolver: true,
                    parent_parameter: None,
                    info_parameter: None,
                    arguments: vec![ArgumentDefinition {
                        name: "count".to_string(),
                        graphql_name: None,
                        graphql_type: GraphQLType::Named("Int".to_string()),
                        has_default: true,
                        default_value: Some("3".to_string()),
                        description: None,
                        deprecation_reason: Some("no longer used".to_string()),
                        is_maybe: false,
                        applied_directives: Vec::new(),
                    }],
                    applied_directives: Vec::new(),
                }],
                applied_directives: Vec::new(),
            },
        );

        let sdl = render_sdl(&schema);
        // `= 3` sits between the type and the directive, per the `InputValueDefinition` grammar.
        assert!(
            sdl.contains("oldField(count: Int = 3 @deprecated(reason: \"no longer used\")): String!"),
            "{sdl}"
        );
    }

    #[test]
    fn renders_an_argument_default_so_a_defaulted_argument_does_not_read_as_required() {
        let mut schema = empty_schema();
        schema.types.insert(
            "Query".to_string(),
            TypeDefinition {
                kind: TypeKind::Type,
                name: "Query".to_string(),
                description: None,
                one_of: false,
                interfaces: Vec::new(),
                enum_values: Vec::new(),
                applied_directives: Vec::new(),
                fields: vec![FieldDefinition {
                    name: "search".to_string(),
                    graphql_name: None,
                    graphql_type: GraphQLType::NonNull(Box::new(GraphQLType::Named("String".to_string()))),
                    description: None,
                    deprecation_reason: None,
                    default_value: None,
                    is_maybe: false,
                    has_resolver: true,
                    parent_parameter: None,
                    info_parameter: None,
                    applied_directives: Vec::new(),
                    arguments: vec![
                        // Non-null *with* a default: optional to the server, and must read that way
                        // to every schema consumer too -- `Int!` alone means required.
                        ArgumentDefinition {
                            name: "limit".to_string(),
                            graphql_name: None,
                            graphql_type: GraphQLType::NonNull(Box::new(GraphQLType::Named("Int".to_string()))),
                            has_default: true,
                            default_value: Some("10".to_string()),
                            description: None,
                            deprecation_reason: None,
                            is_maybe: false,
                            applied_directives: Vec::new(),
                        },
                        // A default that had no faithful literal spelling: nothing is rendered
                        // rather than something wrong.
                        ArgumentDefinition {
                            name: "cursor".to_string(),
                            graphql_name: None,
                            graphql_type: GraphQLType::Named("String".to_string()),
                            has_default: true,
                            default_value: None,
                            is_maybe: false,
                            description: None,
                            deprecation_reason: None,
                            applied_directives: Vec::new(),
                        },
                    ],
                }],
            },
        );

        let sdl = render_sdl(&schema);
        assert!(sdl.contains("search(limit: Int! = 10, cursor: String): String!"), "{sdl}");
    }

    #[test]
    fn renders_schema_directive_and_operation_directive_declarations() {
        let mut schema = empty_schema();
        schema.schema_directives.insert(
            "keys".to_string(),
            SchemaDirectiveDefinition {
                name: "keys".to_string(),
                description: Some("Marks a type's key fields".to_string()),
                locations: vec![SchemaDirectiveLocation::Object],
                fields: vec![DirectiveFieldDefinition {
                    name: "fields".to_string(),
                    graphql_name: None,
                    graphql_type: GraphQLType::NonNull(Box::new(GraphQLType::Named("String".to_string()))),
                }],
                repeatable: false,
            },
        );
        schema.operation_directives.insert(
            "turnUppercase".to_string(),
            OperationDirectiveDefinition {
                name: "turnUppercase".to_string(),
                description: None,
                locations: vec![OperationDirectiveLocation::Field],
                value_parameter: Some("value".to_string()),
                info_parameter: None,
                arguments: Vec::new(),
            },
        );

        let sdl = render_sdl(&schema);
        insta::assert_snapshot!(sdl, @r###"
        schema {
          query: Query
        }

        directive @turnUppercase on FIELD

        """Marks a type's key fields"""
        directive @keys(fields: String!) on OBJECT
        "###);
    }

    #[test]
    fn renders_a_repeatable_directive_declaration() {
        let mut schema = empty_schema();
        schema.schema_directives.insert(
            "key".to_string(),
            SchemaDirectiveDefinition {
                name: "key".to_string(),
                description: None,
                locations: vec![SchemaDirectiveLocation::Object, SchemaDirectiveLocation::Interface],
                fields: vec![DirectiveFieldDefinition {
                    name: "fields".to_string(),
                    graphql_name: None,
                    graphql_type: GraphQLType::NonNull(Box::new(GraphQLType::Named("FieldSet".to_string()))),
                }],
                repeatable: true,
            },
        );

        let sdl = render_sdl(&schema);
        assert!(sdl.contains("directive @key(fields: FieldSet!) repeatable on OBJECT | INTERFACE"));
    }

    #[test]
    fn renders_applied_directives_on_the_schema_block() {
        let mut schema = empty_schema();
        schema.schema_applied_directives = vec![AppliedDirective {
            name: "link".to_string(),
            arguments: vec![(
                "url".to_string(),
                serde_json::json!("https://specs.apollo.dev/federation/v2.6"),
            )],
        }];

        let sdl = render_sdl(&schema);
        insta::assert_snapshot!(sdl, @r###"
        schema @link(url: "https://specs.apollo.dev/federation/v2.6") {
          query: Query
        }
        "###);
    }

    fn query_with_snake_case_field() -> TypeDefinition {
        TypeDefinition {
            kind: TypeKind::Type,
            name: "Query".to_string(),
            description: None,
            one_of: false,
            interfaces: Vec::new(),
            enum_values: Vec::new(),
            fields: vec![FieldDefinition {
                name: "post_by_slug".to_string(),
                graphql_name: None,
                graphql_type: GraphQLType::Named("Post".to_string()),
                description: None,
                deprecation_reason: None,
                default_value: None,
                is_maybe: false,
                has_resolver: true,
                parent_parameter: None,
                info_parameter: None,
                arguments: vec![ArgumentDefinition {
                    name: "post_id".to_string(),
                    graphql_name: None,
                    graphql_type: GraphQLType::NonNull(Box::new(GraphQLType::Named("ID".to_string()))),
                    has_default: false,
                    default_value: None,
                    is_maybe: false,
                    description: None,
                    deprecation_reason: None,
                    applied_directives: Vec::new(),
                }],
                applied_directives: Vec::new(),
            }],
            applied_directives: Vec::new(),
        }
    }

    #[test]
    fn renders_snake_case_field_and_argument_names_as_camel_case_by_default() {
        let mut schema = empty_schema();
        schema.types.insert("Query".to_string(), query_with_snake_case_field());

        let sdl = render_sdl(&schema);
        assert!(sdl.contains("postBySlug(postId: ID!): Post"));
    }

    #[test]
    fn respects_auto_camel_case_false() {
        let mut schema = empty_schema();
        schema.auto_camel_case = false;
        schema.types.insert("Query".to_string(), query_with_snake_case_field());

        let sdl = render_sdl(&schema);
        assert!(sdl.contains("post_by_slug(post_id: ID!): Post"));
    }
}
