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

use std::collections::HashMap;

use async_graphql_parser::Positioned;
use async_graphql_parser::types::{
    Directive, ExecutableDocument, Field, FragmentDefinition, OperationType, Selection, SelectionSet,
};
use async_graphql_value::{Name, Value};

use crate::document::select_operation;
use crate::error::{ErrorCode, GraphQLError, GraphQLResult, Location};
use crate::naming::to_camel_case;
use crate::schema::{ArgumentDefinition, CompiledSchema, FieldDefinition, GraphQLType, OperationDirectiveLocation, TypeDefinition, TypeKind};

fn operation_directive_location_str(location: OperationDirectiveLocation) -> String {
    serde_json::to_value(location)
        .ok()
        .and_then(|value| value.as_str().map(str::to_string))
        .unwrap_or_default()
}

fn error_at(message: impl Into<String>, code: ErrorCode, pos: async_graphql_parser::Pos) -> Box<GraphQLError> {
    Box::new(GraphQLError::new(message, code).with_locations(vec![Location::from(pos)]))
}

/// Checks a built-in incremental-delivery directive's (`@defer`/`@stream`) own arguments against a
/// fixed, hardcoded shape -- these are never schema-registered (like `@skip`/`@include`, they're
/// always legal, built in), so there's no `OperationDirectiveDefinition` to check against the way
/// `check_arguments` does for a real custom directive; this is a small, self-contained stand-in
/// reusing `check_value_matches_type` for the actual per-argument type-checking.
fn validate_incremental_directive_arguments(
    arguments: &[(Positioned<Name>, Positioned<Value>)],
    known: &[(&str, GraphQLType)],
    directive_name: &str,
    schema: &CompiledSchema,
) -> GraphQLResult<()> {
    for (arg_name, arg_value) in arguments {
        let name = arg_name.node.as_str();
        let Some((_, expected_type)) = known.iter().find(|(known_name, _)| *known_name == name) else {
            return Err(error_at(
                format!("unknown argument '{name}' on '@{directive_name}'"),
                ErrorCode::UnknownArgument,
                arg_name.pos,
            ));
        };

        check_value_matches_type(&arg_value.node, expected_type, schema).map_err(|reason| {
            error_at(
                format!("argument '{name}' on '@{directive_name}' {reason}"),
                ErrorCode::ArgumentTypeMismatch,
                arg_value.pos,
            )
        })?;
    }
    Ok(())
}

fn defer_argument_shape() -> Vec<(&'static str, GraphQLType)> {
    vec![("if", GraphQLType::Named("Boolean".to_string())), ("label", GraphQLType::Named("String".to_string()))]
}

fn stream_argument_shape() -> Vec<(&'static str, GraphQLType)> {
    vec![
        ("if", GraphQLType::Named("Boolean".to_string())),
        ("label", GraphQLType::Named("String".to_string())),
        ("initialCount", GraphQLType::Named("Int".to_string())),
    ]
}

/// Checks every directive on a selection against the schema's registered custom operation
/// directives (`@skip`/`@include` are always legal, built in, never registered as such; `@defer`/
/// `@stream` are also always legal and built in, but -- unlike skip/include -- have real argument
/// shapes and location constraints of their own to check, per `validate_incremental_directive_arguments`
/// above) -- both that the directive is known at all, and that it's used at a location its
/// declaration allows. Argument type-checking for a custom directive's own arguments reuses
/// `check_arguments`, the same logic a field's arguments go through.
fn validate_directives(
    directives: &[Positioned<Directive>],
    location: OperationDirectiveLocation,
    schema: &CompiledSchema,
) -> GraphQLResult<()> {
    for directive in directives {
        let name = directive.node.name.node.as_str();
        if name == "skip" || name == "include" {
            continue;
        }

        if name == "defer" {
            if !matches!(location, OperationDirectiveLocation::InlineFragment | OperationDirectiveLocation::FragmentSpread) {
                return Err(error_at(
                    format!(
                        "directive '@defer' is not allowed at this location ({})",
                        operation_directive_location_str(location)
                    ),
                    ErrorCode::InvalidDirectiveLocation,
                    directive.pos,
                ));
            }
            validate_incremental_directive_arguments(&directive.node.arguments, &defer_argument_shape(), "defer", schema)?;
            continue;
        }

        if name == "stream" {
            if location != OperationDirectiveLocation::Field {
                return Err(error_at(
                    format!(
                        "directive '@stream' is not allowed at this location ({})",
                        operation_directive_location_str(location)
                    ),
                    ErrorCode::InvalidDirectiveLocation,
                    directive.pos,
                ));
            }
            validate_incremental_directive_arguments(&directive.node.arguments, &stream_argument_shape(), "stream", schema)?;
            continue;
        }

        let directive_def = schema.operation_directives.get(name).ok_or_else(|| {
            error_at(
                format!("unknown directive '@{name}'"),
                ErrorCode::InvalidDirectiveLocation,
                directive.pos,
            )
        })?;

        if !directive_def.locations.contains(&location) {
            return Err(error_at(
                format!(
                    "directive '@{name}' is not allowed at this location ({})",
                    operation_directive_location_str(location)
                ),
                ErrorCode::InvalidDirectiveLocation,
                directive.pos,
            ));
        }

        check_arguments(&directive.node.arguments, &directive_def.arguments, name, directive.pos, schema)?;
    }
    Ok(())
}

fn describe_value_kind(value: &Value) -> &'static str {
    match value {
        Value::Variable(_) => "a variable",
        Value::Null => "null",
        Value::Number(_) => "a number",
        Value::String(_) => "a string",
        Value::Boolean(_) => "a boolean",
        Value::Binary(_) => "binary data",
        Value::Enum(_) => "an enum value",
        Value::List(_) => "a list",
        Value::Object(_) => "an object",
    }
}

/// Approximate but principled argument-value type-checking: walks the parsed literal alongside
/// the declared `GraphQLType`, unwrapping `NonNull`/`List` as it goes. Variables are never
/// checked here (their coerced type isn't known without full variable-definition coercion, which
/// is out of this task's scope) -- only literal values in the query document itself.
fn check_value_matches_type(value: &Value, expected: &GraphQLType, schema: &CompiledSchema) -> Result<(), String> {
    if matches!(value, Value::Variable(_)) {
        return Ok(());
    }

    match expected {
        GraphQLType::NonNull(inner) => {
            if matches!(value, Value::Null) {
                return Err("must not be null".to_string());
            }
            check_value_matches_type(value, inner, schema)
        }
        GraphQLType::List(inner) => match value {
            Value::Null => Ok(()),
            Value::List(items) => items.iter().try_for_each(|item| check_value_matches_type(item, inner, schema)),
            _ => Err(format!("expected a list, got {}", describe_value_kind(value))),
        },
        GraphQLType::Named(name) => {
            if matches!(value, Value::Null) {
                return Ok(());
            }
            match name.as_str() {
                "String" | "ID" => match value {
                    Value::String(_) => Ok(()),
                    _ => Err(format!("expected a string, got {}", describe_value_kind(value))),
                },
                "Int" => match value {
                    Value::Number(number) if number.is_i64() || number.is_u64() => Ok(()),
                    Value::Number(_) => Err("expected an integer, got a floating-point number".to_string()),
                    _ => Err(format!("expected an integer, got {}", describe_value_kind(value))),
                },
                "Float" => match value {
                    Value::Number(_) => Ok(()),
                    _ => Err(format!("expected a number, got {}", describe_value_kind(value))),
                },
                "Boolean" => match value {
                    Value::Boolean(_) => Ok(()),
                    _ => Err(format!("expected a boolean, got {}", describe_value_kind(value))),
                },
                _ if schema.scalar_names.contains(name) => Ok(()),
                _ => match schema.types.get(name) {
                    Some(type_def) if type_def.kind == TypeKind::Input => match value {
                        // Each provided field is checked against *that field's* own declared type,
                        // which is what makes a wrong-typed scalar or an invalid enum value caught
                        // one level down rather than only at the top level. A field name with no
                        // matching definition is skipped rather than rejected here -- the spec's
                        // Input Object Field Names / Required Fields rules are separate checks
                        // bramble doesn't implement yet, and inventing a partial version of them
                        // here would report the wrong error for a genuinely unknown field.
                        Value::Object(entries) => entries.iter().try_for_each(|(field_name, field_value)| {
                            match find_field(type_def, field_name.as_str(), schema.auto_camel_case) {
                                Some(field_def) => check_value_matches_type(field_value, &field_def.graphql_type, schema)
                                    .map_err(|error| format!("field '{field_name}': {error}")),
                                None => Ok(()),
                            }
                        }),
                        _ => Err(format!("expected an input object for '{name}', got {}", describe_value_kind(value))),
                    },
                    Some(type_def) if type_def.kind == TypeKind::Enum => match value {
                        // A GraphQL enum literal is an unquoted name (`RED`), which the parser
                        // gives us as `Value::Enum` -- deliberately *not* accepting `Value::String`
                        // here, since `"RED"` is a String literal and the spec keeps the two
                        // distinct (§ Values of Correct Type).
                        Value::Enum(member) => {
                            let member = member.as_str();
                            if type_def
                                .enum_values
                                .iter()
                                .any(|value| value.graphql_name.as_deref().unwrap_or(&value.name) == member)
                            {
                                Ok(())
                            } else {
                                Err(format!("'{member}' is not a valid value for enum '{name}'"))
                            }
                        }
                        _ => Err(format!(
                            "expected a value of enum '{name}', got {}",
                            describe_value_kind(value)
                        )),
                    },
                    _ => Ok(()),
                },
            }
        }
    }
}

/// The GraphQL-facing name an argument/field is actually queried by: its explicit `name=`
/// override if it declared one, else a camelCase rendering of the Python identifier (or the
/// identifier as-is, if `SchemaConfig(auto_camel_case=False)`).
fn argument_key(argument: &ArgumentDefinition, auto_camel_case: bool) -> String {
    if let Some(graphql_name) = &argument.graphql_name {
        return graphql_name.clone();
    }
    if auto_camel_case { to_camel_case(&argument.name) } else { argument.name.clone() }
}

/// Validates a selection/directive's provided arguments against its declared ones: every
/// provided argument must be declared and type-check against its literal value, and every
/// argument the schema marks required (non-null, no default) must be present.
fn check_arguments(
    provided: &[(Positioned<Name>, Positioned<Value>)],
    declared: &[ArgumentDefinition],
    owner_name: &str,
    pos: async_graphql_parser::Pos,
    schema: &CompiledSchema,
) -> GraphQLResult<()> {
    for (arg_name, arg_value) in provided {
        let name = arg_name.node.as_str();
        let argument_def = declared
            .iter()
            .find(|argument| argument_key(argument, schema.auto_camel_case) == name)
            .ok_or_else(|| {
                error_at(
                    format!("unknown argument '{name}' on '{owner_name}'"),
                    ErrorCode::UnknownArgument,
                    arg_name.pos,
                )
            })?;

        check_value_matches_type(&arg_value.node, &argument_def.graphql_type, schema).map_err(|reason| {
            error_at(
                format!("argument '{name}' on '{owner_name}' {reason}"),
                ErrorCode::ArgumentTypeMismatch,
                arg_value.pos,
            )
        })?;
    }

    for argument_def in declared {
        let is_required = !argument_def.graphql_type.is_nullable() && !argument_def.has_default;
        let key = argument_key(argument_def, schema.auto_camel_case);
        if is_required && !provided.iter().any(|(name, _)| name.node.as_str() == key) {
            return Err(error_at(
                format!("missing required argument '{key}' on '{owner_name}'"),
                ErrorCode::ArgumentTypeMismatch,
                pos,
            ));
        }
    }

    Ok(())
}

/// Whether `graphql_type` is a list at any nullability -- `[T]`, `[T]!`, both count; `T`/`T!`
/// (a bare named type, even non-null) doesn't.
fn is_list_type(graphql_type: &GraphQLType) -> bool {
    match graphql_type {
        GraphQLType::List(_) => true,
        GraphQLType::NonNull(inner) => is_list_type(inner),
        GraphQLType::Named(_) => false,
    }
}

fn field_key(field: &FieldDefinition, auto_camel_case: bool) -> String {
    if let Some(graphql_name) = &field.graphql_name {
        return graphql_name.clone();
    }
    if auto_camel_case { to_camel_case(&field.name) } else { field.name.clone() }
}

fn find_field<'a>(type_def: &'a TypeDefinition, name: &str, auto_camel_case: bool) -> Option<&'a FieldDefinition> {
    type_def.fields.iter().find(|field| field_key(field, auto_camel_case) == name)
}

fn validate_field(
    field: &Positioned<Field>,
    parent_type: &TypeDefinition,
    schema: &CompiledSchema,
    fragments: &HashMap<Name, Positioned<FragmentDefinition>>,
    spread_chain: &mut Vec<String>,
) -> GraphQLResult<()> {
    validate_directives(&field.node.directives, OperationDirectiveLocation::Field, schema)?;

    let field_name = field.node.name.node.as_str();
    if field_name == "__typename" {
        return Ok(());
    }

    let field_def = find_field(parent_type, field_name, schema.auto_camel_case).ok_or_else(|| {
        error_at(
            format!("field '{field_name}' does not exist on type '{}'", parent_type.name),
            ErrorCode::UnknownField,
            field.pos,
        )
    })?;

    check_arguments(&field.node.arguments, &field_def.arguments, field_name, field.pos, schema)?;

    if let Some(stream_directive) = field.node.directives.iter().find(|directive| directive.node.name.node.as_str() == "stream")
        && !is_list_type(&field_def.graphql_type)
    {
        return Err(error_at(
            format!(
                "directive '@stream' can only be applied to a list-typed field, but '{field_name}' is '{}'",
                field_def.graphql_type.to_sdl_string()
            ),
            ErrorCode::InvalidDirectiveLocation,
            stream_directive.pos,
        ));
    }

    if !field.node.selection_set.node.items.is_empty()
        && let Some(nested_type) = schema.types.get(field_def.graphql_type.inner_name())
    {
        validate_selection_set(&field.node.selection_set.node, nested_type, schema, fragments, spread_chain)?;
    }

    Ok(())
}

/// The spec's "Fragment spreads must not form cycles" rule (§ Validation). `spread_chain` holds
/// the fragment names currently being expanded on *this* path down the tree -- a name already on
/// it means expanding it again would recurse forever. Deliberately a stack, popped on the way back
/// out, not a cumulative "already seen" set: spreading the same fragment twice in sibling
/// positions (`{ ...A ...A }`, or a diamond) is perfectly legal and must keep validating, only a
/// genuine cycle is an error.
///
/// Without this, a self-referencing (`fragment A on T { ...A }`) or mutually-recursive
/// (`A -> B -> A`) document sends this recursion into an unbounded loop -- reachable from any
/// unauthenticated request, since every HTTP adapter validates raw client input.
fn check_fragment_cycle(
    fragment_name: &Name,
    spread_chain: &[String],
    pos: async_graphql_parser::Pos,
) -> GraphQLResult<()> {
    if spread_chain.iter().any(|seen| seen == fragment_name.as_str()) {
        return Err(error_at(
            format!("Fragment cycle detected involving '{fragment_name}'"),
            ErrorCode::InvalidFragmentTarget,
            pos,
        ));
    }
    Ok(())
}

fn validate_selection_set(
    selection_set: &SelectionSet,
    parent_type: &TypeDefinition,
    schema: &CompiledSchema,
    fragments: &HashMap<Name, Positioned<FragmentDefinition>>,
    spread_chain: &mut Vec<String>,
) -> GraphQLResult<()> {
    for selection in &selection_set.items {
        match &selection.node {
            Selection::Field(field) => validate_field(field, parent_type, schema, fragments, spread_chain)?,
            Selection::InlineFragment(inline) => {
                validate_directives(&inline.node.directives, OperationDirectiveLocation::InlineFragment, schema)?;

                let target_type = match &inline.node.type_condition {
                    Some(condition) => {
                        let name = condition.node.on.node.as_str();
                        schema.types.get(name).ok_or_else(|| {
                            error_at(
                                format!("inline fragment targets unknown type '{name}'"),
                                ErrorCode::InvalidFragmentTarget,
                                condition.pos,
                            )
                        })?
                    }
                    None => parent_type,
                };
                validate_selection_set(&inline.node.selection_set.node, target_type, schema, fragments, spread_chain)?;
            }
            Selection::FragmentSpread(spread) => {
                validate_directives(&spread.node.directives, OperationDirectiveLocation::FragmentSpread, schema)?;

                let fragment_name = &spread.node.fragment_name.node;
                let fragment = fragments.get(fragment_name).ok_or_else(|| {
                    error_at(
                        format!("undefined fragment '{fragment_name}'"),
                        ErrorCode::InvalidFragmentTarget,
                        spread.pos,
                    )
                })?;

                check_fragment_cycle(fragment_name, spread_chain, spread.pos)?;

                let target_name = fragment.node.type_condition.node.on.node.as_str();
                let target_type = schema.types.get(target_name).ok_or_else(|| {
                    error_at(
                        format!("fragment '{fragment_name}' targets unknown type '{target_name}'"),
                        ErrorCode::InvalidFragmentTarget,
                        fragment.node.type_condition.pos,
                    )
                })?;

                spread_chain.push(fragment_name.to_string());
                let result =
                    validate_selection_set(&fragment.node.selection_set.node, target_type, schema, fragments, spread_chain);
                spread_chain.pop();
                result?;
            }
        }
    }
    Ok(())
}

fn root_type_name(operation_type: OperationType, schema: &CompiledSchema) -> GraphQLResult<&str> {
    match operation_type {
        OperationType::Query => Ok(&schema.query_type_name),
        OperationType::Mutation => schema.mutation_type_name.as_deref().ok_or_else(|| {
            Box::new(GraphQLError::new(
                "document contains a mutation, but the schema has no mutation type",
                ErrorCode::GraphqlValidationFailed,
            ))
        }),
        OperationType::Subscription => schema.subscription_type_name.as_deref().ok_or_else(|| {
            Box::new(GraphQLError::new(
                "document contains a subscription, but the schema has no subscription type",
                ErrorCode::GraphqlValidationFailed,
            ))
        }),
    }
}

/// Validates a parsed query document's chosen operation against a `CompiledSchema` (§7a): every
/// requested field exists on its parent type, arguments are declared and type-check, directives
/// are used at legal locations, and fragment spreads/inline fragments target real types. Pure
/// schema-shape checking -- no Python domain objects involved, matching `is_type_of`/
/// `resolve_type` being execution-time-only (Tasks 5/6).
pub fn validate_query(document: &ExecutableDocument, schema: &CompiledSchema, operation_name: Option<&str>) -> GraphQLResult<()> {
    let operation = select_operation(document, operation_name)?;
    let root_name = root_type_name(operation.ty, schema)?;

    let root_type = schema.types.get(root_name).ok_or_else(|| {
        Box::new(GraphQLError::new(
            format!("schema's root type '{root_name}' is not registered"),
            ErrorCode::GraphqlValidationFailed,
        ))
    })?;

    validate_selection_set(&operation.selection_set.node, root_type, schema, &document.fragments, &mut Vec::new())
}

#[cfg(test)]
mod tests {
    use std::collections::HashSet;

    use super::*;
    use crate::parser::parse_document;
    use crate::persisted_query::PersistedQueryCache;

    fn named(name: &str) -> GraphQLType {
        GraphQLType::NonNull(Box::new(GraphQLType::Named(name.to_string())))
    }

    fn nullable(name: &str) -> GraphQLType {
        GraphQLType::Named(name.to_string())
    }

    fn list_of(name: &str) -> GraphQLType {
        GraphQLType::NonNull(Box::new(GraphQLType::List(Box::new(named(name)))))
    }

    fn argument(name: &str, graphql_type: GraphQLType, has_default: bool) -> ArgumentDefinition {
        ArgumentDefinition {
            name: name.to_string(),
            graphql_name: None,
            graphql_type,
            has_default,
            description: None,
            deprecation_reason: None,
            applied_directives: Vec::new(),
        }
    }

    fn field(name: &str, graphql_type: GraphQLType, arguments: Vec<ArgumentDefinition>) -> FieldDefinition {
        FieldDefinition {
            name: name.to_string(),
            graphql_name: None,
            graphql_type,
            description: None,
            has_resolver: !arguments.is_empty(),
            parent_parameter: None,
            info_parameter: None,
            arguments,
            applied_directives: Vec::new(),
        }
    }

    fn object(name: &str, fields: Vec<FieldDefinition>) -> TypeDefinition {
        TypeDefinition {
            kind: TypeKind::Type,
            name: name.to_string(),
            description: None,
            one_of: false,
            interfaces: Vec::new(),
            enum_values: Vec::new(),
            fields,
            applied_directives: Vec::new(),
        }
    }

    /// A small blog-shaped schema covering the pieces validation actually branches on: an object
    /// with a nested object field, a list field, a scalar leaf, and an argument with a default.
    fn test_schema() -> CompiledSchema {
        let mut types = HashMap::new();
        types.insert(
            "Query".to_string(),
            object(
                "Query",
                vec![
                    field("user", named("User"), vec![argument("id", named("ID"), false)]),
                    field("search", list_of("User"), vec![argument("limit", named("Int"), true)]),
                    field("motto", named("String"), Vec::new()),
                ],
            ),
        );
        types.insert(
            "User".to_string(),
            object(
                "User",
                vec![
                    field("id", named("ID"), Vec::new()),
                    field("name", named("String"), Vec::new()),
                    field("bio", nullable("String"), Vec::new()),
                    field("posts", list_of("Post"), Vec::new()),
                ],
            ),
        );
        types.insert(
            "Post".to_string(),
            object("Post", vec![field("id", named("ID"), Vec::new()), field("author", named("User"), Vec::new())]),
        );

        CompiledSchema {
            types,
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

    fn validate(query: &str) -> GraphQLResult<()> {
        let document = parse_document(query).expect("query parses");
        validate_query(&document, &test_schema(), None)
    }

    fn error_message(query: &str) -> String {
        validate(query).expect_err("expected a validation error").message
    }

    #[test]
    fn a_self_referencing_fragment_is_rejected_rather_than_looping_forever() {
        let message = error_message("query { user(id: \"1\") { ...A } } fragment A on User { name ...A }");
        assert!(message.contains("Fragment cycle detected involving 'A'"), "unexpected message: {message}");
    }

    #[test]
    fn a_mutually_recursive_fragment_pair_is_rejected() {
        let message = error_message(
            "query { user(id: \"1\") { ...A } } fragment A on User { ...B } fragment B on User { ...A }",
        );
        assert!(message.contains("Fragment cycle detected"), "unexpected message: {message}");
    }

    #[test]
    fn a_cycle_reached_through_a_nested_field_is_rejected() {
        let message = error_message(
            "query { user(id: \"1\") { ...A } } fragment A on User { posts { author { ...A } } }",
        );
        assert!(message.contains("Fragment cycle detected involving 'A'"), "unexpected message: {message}");
    }

    #[test]
    fn a_cycle_reached_through_an_inline_fragment_is_rejected() {
        let message = error_message(
            "query { user(id: \"1\") { ...A } } fragment A on User { ... on User { ...A } }",
        );
        assert!(message.contains("Fragment cycle detected involving 'A'"), "unexpected message: {message}");
    }

    #[test]
    fn the_same_fragment_spread_twice_in_sibling_positions_is_not_a_cycle() {
        // The guard is a stack popped on the way back out, not a cumulative seen-set -- spreading
        // one fragment from two independent positions is legal and must keep validating.
        validate("query { user(id: \"1\") { ...A posts { author { ...A } } } } fragment A on User { name }")
            .expect("sibling spreads of the same fragment are valid");
    }

    #[test]
    fn a_deep_acyclic_fragment_chain_still_validates() {
        validate(
            "query { user(id: \"1\") { ...A } } \
             fragment A on User { ...B } fragment B on User { ...C } fragment C on User { name }",
        )
        .expect("an acyclic chain is valid");
    }

    #[test]
    fn an_undefined_fragment_is_still_reported_as_undefined_not_as_a_cycle() {
        let message = error_message("query { user(id: \"1\") { ...Missing } }");
        assert!(message.contains("undefined fragment 'Missing'"), "unexpected message: {message}");
    }
}
