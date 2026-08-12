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
    Directive, ExecutableDocument, Field, FragmentDefinition, OperationDefinition, OperationType, Selection, SelectionSet,
};
use async_graphql_value::{Name, Value};

use crate::document::select_operation;
use crate::error::{ErrorCode, GraphQLError, GraphQLResult, Location};
use crate::naming::to_camel_case;
use crate::schema::{
    ArgumentDefinition, CompiledSchema, FieldDefinition, GraphQLType, OperationDirectiveLocation, TypeDefinition, TypeKind,
};

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
    variables: &VariableTypes,
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

        check_value_matches_type(&arg_value.node, expected_type, schema, variables, true).map_err(|reason| {
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
    vec![
        ("if", GraphQLType::Named("Boolean".to_string())),
        ("label", GraphQLType::Named("String".to_string())),
    ]
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
    variables: &VariableTypes,
) -> GraphQLResult<()> {
    for directive in directives {
        let name = directive.node.name.node.as_str();
        if name == "skip" || name == "include" {
            continue;
        }

        if name == "defer" {
            if !matches!(
                location,
                OperationDirectiveLocation::InlineFragment | OperationDirectiveLocation::FragmentSpread
            ) {
                return Err(error_at(
                    format!(
                        "directive '@defer' is not allowed at this location ({})",
                        operation_directive_location_str(location)
                    ),
                    ErrorCode::InvalidDirectiveLocation,
                    directive.pos,
                ));
            }
            validate_incremental_directive_arguments(
                &directive.node.arguments,
                &defer_argument_shape(),
                "defer",
                schema,
                variables,
            )?;
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
            validate_incremental_directive_arguments(
                &directive.node.arguments,
                &stream_argument_shape(),
                "stream",
                schema,
                variables,
            )?;
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

        check_arguments(
            &directive.node.arguments,
            &directive_def.arguments,
            name,
            directive.pos,
            schema,
            variables,
        )?;
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

/// A variable's declared type plus whether it has a default, keyed by name -- what the spec's
/// "All Variable Usages Are Allowed" rule needs at each usage site.
pub type VariableTypes = HashMap<String, (GraphQLType, bool)>;

/// Converts the parser's own `Type` into the schema IR's `GraphQLType`, so a variable's declared
/// type can be compared against the type expected where it is used.
fn convert_parser_type(parser_type: &async_graphql_parser::types::Type) -> GraphQLType {
    let base = match &parser_type.base {
        async_graphql_parser::types::BaseType::Named(name) => GraphQLType::Named(name.as_str().to_string()),
        async_graphql_parser::types::BaseType::List(inner) => GraphQLType::List(Box::new(convert_parser_type(inner))),
    };
    if parser_type.nullable {
        base
    } else {
        GraphQLType::NonNull(Box::new(base))
    }
}

/// The spec's `IsVariableUsageAllowed`: a variable's declared type must be usable where it appears.
///
/// The one non-obvious rule is the nullability relaxation: a nullable variable *is* allowed in a
/// non-null position provided it declares a default, since the default guarantees a value will be
/// present. Everything else is ordinary structural comparison -- list nesting must match, and named
/// types must be identical (GraphQL has no subtyping for input positions).
fn is_variable_usage_allowed(declared: &GraphQLType, has_default: bool, expected: &GraphQLType) -> bool {
    match (declared, expected) {
        (GraphQLType::NonNull(declared_inner), GraphQLType::NonNull(expected_inner)) => {
            is_variable_usage_allowed(declared_inner, has_default, expected_inner)
        }
        // A non-null variable is always acceptable where a nullable value is wanted.
        (GraphQLType::NonNull(declared_inner), expected) => is_variable_usage_allowed(declared_inner, has_default, expected),
        (declared, GraphQLType::NonNull(expected_inner)) => {
            has_default && is_variable_usage_allowed(declared, has_default, expected_inner)
        }
        (GraphQLType::List(declared_inner), GraphQLType::List(expected_inner)) => {
            is_variable_usage_allowed(declared_inner, has_default, expected_inner)
        }
        (GraphQLType::Named(declared_name), GraphQLType::Named(expected_name)) => declared_name == expected_name,
        _ => false,
    }
}

/// Argument-value type-checking: walks the parsed literal alongside the declared `GraphQLType`,
/// unwrapping `NonNull`/`List` as it goes. A `$variable` usage is checked against its *declared*
/// type rather than a value (there is no value yet at validation time), per the spec's
/// "All Variable Usages Are Allowed" rule -- which also catches a usage of a variable the operation
/// never declared.
fn check_value_matches_type(
    value: &Value,
    expected: &GraphQLType,
    schema: &CompiledSchema,
    variables: &VariableTypes,
    location_has_default: bool,
) -> Result<(), String> {
    if let Value::Variable(name) = value {
        let Some((declared, has_default)) = variables.get(name.as_str()) else {
            return Err(format!("references undefined variable '${name}'"));
        };
        // Per the spec, a nullable variable is allowed in a non-null position when *either* the
        // variable declares a default or the location itself does -- both guarantee a value is
        // present. Omitting the location half rejects the perfectly ordinary
        // `search(limit: Int! = 10)` called as `search(limit: $limit)` with `$limit: Int`.
        if !is_variable_usage_allowed(declared, *has_default || location_has_default, expected) {
            return Err(format!(
                "expects '{}', but variable '${name}' is declared as '{}'",
                expected.to_sdl_string(),
                declared.to_sdl_string()
            ));
        }
        return Ok(());
    }

    match expected {
        GraphQLType::NonNull(inner) => {
            if matches!(value, Value::Null) {
                return Err("must not be null".to_string());
            }
            check_value_matches_type(value, inner, schema, variables, location_has_default)
        }
        GraphQLType::List(inner) => match value {
            Value::Null => Ok(()),
            Value::List(items) => items
                .iter()
                .try_for_each(|item| check_value_matches_type(item, inner, schema, variables, false)),
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
                                Some(field_def) => check_value_matches_type(
                                    field_value,
                                    &field_def.graphql_type,
                                    schema,
                                    variables,
                                    field_def.default_value.is_some(),
                                )
                                .map_err(|error| format!("field '{field_name}': {error}")),
                                None => Ok(()),
                            }
                        }),
                        _ => Err(format!(
                            "expected an input object for '{name}', got {}",
                            describe_value_kind(value)
                        )),
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
    if auto_camel_case {
        to_camel_case(&argument.name)
    } else {
        argument.name.clone()
    }
}

/// The spec's "Argument Uniqueness" rule: one field or directive may not be given the same
/// argument name twice. The parser keeps arguments in a `Vec`, so unlike duplicate operation or
/// fragment *names* (which it collapses into a `HashMap` before validation ever sees them) this
/// one is still detectable here.
fn check_argument_uniqueness(provided: &[(Positioned<Name>, Positioned<Value>)], owner_name: &str) -> GraphQLResult<()> {
    let mut seen: Vec<&str> = Vec::with_capacity(provided.len());
    for (arg_name, _) in provided {
        let name = arg_name.node.as_str();
        if seen.contains(&name) {
            return Err(error_at(
                format!("argument '{name}' is provided more than once on '{owner_name}'"),
                ErrorCode::UnknownArgument,
                arg_name.pos,
            ));
        }
        seen.push(name);
    }
    Ok(())
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
    variables: &VariableTypes,
) -> GraphQLResult<()> {
    check_argument_uniqueness(provided, owner_name)?;

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

        check_value_matches_type(
            &arg_value.node,
            &argument_def.graphql_type,
            schema,
            variables,
            argument_def.has_default,
        )
        .map_err(|reason| {
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

/// The concrete object types a named type could actually resolve to at runtime -- an object is
/// just itself, an interface is every type implementing it, a union is its declared members.
/// Used for the spec's fragment-spread-possibility rule; an unregistered name yields an empty set,
/// which callers treat as "unknown, don't reject".
fn possible_types(name: &str, schema: &CompiledSchema) -> Vec<String> {
    if let Some(union_def) = schema.unions.get(name) {
        return union_def.member_names.clone();
    }
    match schema.types.get(name) {
        Some(type_def) if type_def.kind == TypeKind::Interface => schema
            .types
            .values()
            .filter(|candidate| candidate.interfaces.iter().any(|implemented| implemented == name))
            .map(|candidate| candidate.name.clone())
            .collect(),
        Some(type_def) => vec![type_def.name.clone()],
        None => Vec::new(),
    }
}

/// The spec's "Fragment Spread Is Possible" rule: a fragment can only be spread somewhere its type
/// condition could actually apply, i.e. the concrete types it covers and the ones the parent type
/// covers overlap. Spreading `... on Dog` inside a `Cat` selection can never match anything, so the
/// selections inside are dead code and the spec makes it an error rather than a silent no-op.
///
/// Deliberately lenient when either side has no known possible types (an unregistered name, or an
/// interface nothing implements yet): reporting "impossible" for a type the schema simply doesn't
/// describe would turn a registration gap into a confusing query error.
fn check_fragment_is_possible(
    condition_name: &str,
    parent_type: &TypeDefinition,
    pos: async_graphql_parser::Pos,
    schema: &CompiledSchema,
) -> GraphQLResult<()> {
    let condition_types = possible_types(condition_name, schema);
    let parent_types = possible_types(&parent_type.name, schema);
    if condition_types.is_empty() || parent_types.is_empty() {
        return Ok(());
    }

    if condition_types.iter().any(|candidate| parent_types.contains(candidate)) {
        return Ok(());
    }

    Err(error_at(
        format!(
            "fragment on '{condition_name}' can never apply to '{}' -- they share no possible types",
            parent_type.name
        ),
        ErrorCode::InvalidFragmentTarget,
        pos,
    ))
}

/// Whether a named type is a leaf (scalar or enum) rather than a composite (object/interface/
/// union). Drives the spec's "Leaf Field Selections" rule below. `None` means the name isn't
/// something the schema describes at all, which callers treat as "unknown, don't reject".
fn is_leaf_type(name: &str, schema: &CompiledSchema) -> Option<bool> {
    if matches!(name, "String" | "Int" | "Float" | "Boolean" | "ID") || schema.scalar_names.contains(name) {
        return Some(true);
    }
    if schema.unions.contains_key(name) {
        return Some(false);
    }
    schema.types.get(name).map(|type_def| type_def.kind == TypeKind::Enum)
}

fn field_key(field: &FieldDefinition, auto_camel_case: bool) -> String {
    if let Some(graphql_name) = &field.graphql_name {
        return graphql_name.clone();
    }
    if auto_camel_case {
        to_camel_case(&field.name)
    } else {
        field.name.clone()
    }
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
    variables: &VariableTypes,
) -> GraphQLResult<()> {
    validate_directives(&field.node.directives, OperationDirectiveLocation::Field, schema, variables)?;

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

    check_arguments(
        &field.node.arguments,
        &field_def.arguments,
        field_name,
        field.pos,
        schema,
        variables,
    )?;

    if let Some(stream_directive) = field
        .node
        .directives
        .iter()
        .find(|directive| directive.node.name.node.as_str() == "stream")
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

    // The spec's "Leaf Field Selections" rule, both halves. This used to be a bare
    // `if let Some(nested_type) = schema.types.get(...)`, which silently accepted *both* mistakes:
    // a sub-selection on a scalar (the lookup misses, so the whole check was skipped) and a missing
    // sub-selection on an object (nothing checked emptiness at all).
    let inner_name = field_def.graphql_type.inner_name();
    let has_selections = !field.node.selection_set.node.items.is_empty();
    match is_leaf_type(inner_name, schema) {
        Some(true) if has_selections => {
            return Err(error_at(
                format!("field '{field_name}' is of leaf type '{inner_name}' and cannot have a selection set"),
                ErrorCode::UnknownField,
                field.node.selection_set.pos,
            ));
        }
        Some(false) if !has_selections => {
            return Err(error_at(
                format!("field '{field_name}' is of composite type '{inner_name}' and must have a selection set"),
                ErrorCode::UnknownField,
                field.pos,
            ));
        }
        _ => {}
    }

    if has_selections && let Some(nested_type) = schema.types.get(inner_name) {
        validate_selection_set(
            &field.node.selection_set.node,
            nested_type,
            schema,
            fragments,
            spread_chain,
            variables,
        )?;
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
fn check_fragment_cycle(fragment_name: &Name, spread_chain: &[String], pos: async_graphql_parser::Pos) -> GraphQLResult<()> {
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
    variables: &VariableTypes,
) -> GraphQLResult<()> {
    for selection in &selection_set.items {
        match &selection.node {
            Selection::Field(field) => validate_field(field, parent_type, schema, fragments, spread_chain, variables)?,
            Selection::InlineFragment(inline) => {
                validate_directives(
                    &inline.node.directives,
                    OperationDirectiveLocation::InlineFragment,
                    schema,
                    variables,
                )?;

                let target_type = match &inline.node.type_condition {
                    Some(condition) => {
                        let name = condition.node.on.node.as_str();
                        let target = schema.types.get(name).ok_or_else(|| {
                            error_at(
                                format!("inline fragment targets unknown type '{name}'"),
                                ErrorCode::InvalidFragmentTarget,
                                condition.pos,
                            )
                        })?;
                        check_fragment_is_possible(name, parent_type, condition.pos, schema)?;
                        target
                    }
                    None => parent_type,
                };
                validate_selection_set(
                    &inline.node.selection_set.node,
                    target_type,
                    schema,
                    fragments,
                    spread_chain,
                    variables,
                )?;
            }
            Selection::FragmentSpread(spread) => {
                validate_directives(
                    &spread.node.directives,
                    OperationDirectiveLocation::FragmentSpread,
                    schema,
                    variables,
                )?;

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
                check_fragment_is_possible(target_name, parent_type, spread.pos, schema)?;

                spread_chain.push(fragment_name.to_string());
                let result = validate_selection_set(
                    &fragment.node.selection_set.node,
                    target_type,
                    schema,
                    fragments,
                    spread_chain,
                    variables,
                );
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

/// The spec's "Variable Uniqueness" rule: one operation may not declare `$x` twice. Detectable
/// because the parser keeps variable definitions in a `Vec` (see `check_argument_uniqueness` for
/// why the analogous operation/fragment-name rules are not).
fn check_variable_uniqueness(operation: &OperationDefinition) -> GraphQLResult<()> {
    let mut seen: Vec<&str> = Vec::with_capacity(operation.variable_definitions.len());
    for definition in &operation.variable_definitions {
        let name = definition.node.name.node.as_str();
        if seen.contains(&name) {
            return Err(error_at(
                format!("variable '${name}' is declared more than once"),
                ErrorCode::GraphqlValidationFailed,
                definition.pos,
            ));
        }
        seen.push(name);
    }
    Ok(())
}

/// Counts the distinct response keys a selection set contributes, following fragment spreads and
/// inline fragments the way `CollectFields` does. Returns `None` when any selection carries
/// `@skip`/`@include`: those depend on variable values validation doesn't have, so the true count
/// isn't knowable here and the caller must not draw conclusions from a guess.
fn root_response_key_count(
    selection_set: &SelectionSet,
    fragments: &HashMap<Name, Positioned<FragmentDefinition>>,
    keys: &mut Vec<String>,
    spread_chain: &mut Vec<String>,
) -> Option<()> {
    for selection in &selection_set.items {
        let directives = match &selection.node {
            Selection::Field(field) => &field.node.directives,
            Selection::InlineFragment(inline) => &inline.node.directives,
            Selection::FragmentSpread(spread) => &spread.node.directives,
        };
        if directives
            .iter()
            .any(|directive| matches!(directive.node.name.node.as_str(), "skip" | "include"))
        {
            return None;
        }

        match &selection.node {
            Selection::Field(field) => {
                let key = field.node.response_key().node.as_str().to_string();
                if !keys.contains(&key) {
                    keys.push(key);
                }
            }
            Selection::InlineFragment(inline) => {
                root_response_key_count(&inline.node.selection_set.node, fragments, keys, spread_chain)?;
            }
            Selection::FragmentSpread(spread) => {
                let name = spread.node.fragment_name.node.as_str();
                // The cycle guard in `validate_selection_set` has not necessarily run yet when this
                // is called, so this walk needs its own -- otherwise the subscription rule would be
                // a second way to reach the unbounded recursion fixed for validation proper.
                if spread_chain.iter().any(|seen| seen == name) {
                    return None;
                }
                let fragment = fragments.get(&Name::new(name))?;
                spread_chain.push(name.to_string());
                let result = root_response_key_count(&fragment.node.selection_set.node, fragments, keys, spread_chain);
                spread_chain.pop();
                result?;
            }
        }
    }
    Some(())
}

/// The spec's "Single Root Field" rule for subscriptions. Enforced here, at validation time, so it
/// surfaces as a proper located validation error rather than only once execution reaches it.
///
/// Only decided when the count is knowable statically: a root selection carrying `@skip`/`@include`
/// could prune down to exactly one field at execution time depending on variables, and rejecting
/// that here would be wrong. `bramble._execution.subscribe_async` keeps its own equivalent check as
/// the post-pruning backstop for exactly that case.
fn check_subscription_single_root_field(
    operation: &OperationDefinition,
    fragments: &HashMap<Name, Positioned<FragmentDefinition>>,
) -> GraphQLResult<()> {
    if operation.ty != OperationType::Subscription {
        return Ok(());
    }

    let mut keys = Vec::new();
    if root_response_key_count(&operation.selection_set.node, fragments, &mut keys, &mut Vec::new()).is_none() {
        return Ok(());
    }

    if keys.len() != 1 {
        return Err(error_at(
            format!(
                "a subscription operation must have exactly one root field, but this one selects {}",
                keys.len()
            ),
            ErrorCode::GraphqlValidationFailed,
            operation.selection_set.pos,
        ));
    }
    Ok(())
}

/// Validates a parsed query document's chosen operation against a `CompiledSchema` (§7a): every
/// requested field exists on its parent type and is selected correctly for its kind (leaf fields
/// bare, composite fields with a selection set), arguments are declared, unique, and type-check,
/// directives are used at legal locations, fragment spreads/inline fragments target real types they
/// could actually apply to and form no cycles, variables are uniquely declared, and a subscription
/// selects exactly one root field. Pure schema-shape checking -- no Python domain objects involved,
/// matching `is_type_of`/`resolve_type` being execution-time-only (Tasks 5/6).
///
/// Variable *usages* are checked against their declared types here. Duplicate operation and
/// fragment *names* need no check: `async-graphql-parser` rejects them while building the document,
/// so a redefinition never reaches this function (verified -- it surfaces as a parse error, not a
/// silent last-one-wins collapse, despite operations and fragments being stored in `HashMap`s).
pub fn validate_query(
    document: &ExecutableDocument,
    schema: &CompiledSchema,
    operation_name: Option<&str>,
) -> GraphQLResult<()> {
    let operation = select_operation(document, operation_name)?;
    let root_name = root_type_name(operation.ty, schema)?;

    let root_type = schema.types.get(root_name).ok_or_else(|| {
        Box::new(GraphQLError::new(
            format!("schema's root type '{root_name}' is not registered"),
            ErrorCode::GraphqlValidationFailed,
        ))
    })?;

    check_variable_uniqueness(operation)?;
    check_subscription_single_root_field(operation, &document.fragments)?;

    let variables: VariableTypes = operation
        .variable_definitions
        .iter()
        .map(|definition| {
            (
                definition.node.name.node.as_str().to_string(),
                (
                    convert_parser_type(&definition.node.var_type.node),
                    definition.node.default_value.is_some(),
                ),
            )
        })
        .collect();

    validate_selection_set(
        &operation.selection_set.node,
        root_type,
        schema,
        &document.fragments,
        &mut Vec::new(),
        &variables,
    )
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
            default_value: has_default.then(|| "10".to_string()),
            is_maybe: false,
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
            deprecation_reason: None,
            default_value: None,
            is_maybe: false,
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
            object(
                "Post",
                vec![
                    field("id", named("ID"), Vec::new()),
                    field("author", named("User"), Vec::new()),
                ],
            ),
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
        assert!(
            message.contains("Fragment cycle detected involving 'A'"),
            "unexpected message: {message}"
        );
    }

    #[test]
    fn a_mutually_recursive_fragment_pair_is_rejected() {
        let message =
            error_message("query { user(id: \"1\") { ...A } } fragment A on User { ...B } fragment B on User { ...A }");
        assert!(message.contains("Fragment cycle detected"), "unexpected message: {message}");
    }

    #[test]
    fn a_cycle_reached_through_a_nested_field_is_rejected() {
        let message = error_message("query { user(id: \"1\") { ...A } } fragment A on User { posts { author { ...A } } }");
        assert!(
            message.contains("Fragment cycle detected involving 'A'"),
            "unexpected message: {message}"
        );
    }

    #[test]
    fn a_cycle_reached_through_an_inline_fragment_is_rejected() {
        let message = error_message("query { user(id: \"1\") { ...A } } fragment A on User { ... on User { ...A } }");
        assert!(
            message.contains("Fragment cycle detected involving 'A'"),
            "unexpected message: {message}"
        );
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
        assert!(
            message.contains("undefined fragment 'Missing'"),
            "unexpected message: {message}"
        );
    }

    // --- Leaf / composite selection sets ------------------------------------------------------

    #[test]
    fn a_selection_set_on_a_scalar_field_is_rejected() {
        // Used to pass: the old code looked the field's type up in `schema.types`, missed (a scalar
        // has no entry), and skipped the check entirely instead of reporting it.
        let message = error_message("query { motto { length } }");
        assert!(message.contains("leaf type 'String'"), "unexpected message: {message}");
    }

    #[test]
    fn a_composite_field_without_a_selection_set_is_rejected() {
        let message = error_message("query { user(id: \"1\") }");
        assert!(message.contains("composite type 'User'"), "unexpected message: {message}");
    }

    #[test]
    fn a_composite_list_field_without_a_selection_set_is_rejected() {
        let message = error_message("query { search }");
        assert!(message.contains("composite type 'User'"), "unexpected message: {message}");
    }

    #[test]
    fn a_correctly_selected_leaf_and_composite_pair_still_validates() {
        validate("query { motto user(id: \"1\") { name bio } }").expect("correct leaf/composite selections are valid");
    }

    #[test]
    fn typename_needs_no_selection_set_even_though_it_is_not_a_declared_field() {
        validate("query { __typename user(id: \"1\") { __typename name } }").expect("__typename is always valid");
    }

    // --- Fragment spread possibility ----------------------------------------------------------

    #[test]
    fn a_fragment_on_an_unrelated_type_is_rejected() {
        let message = error_message("query { user(id: \"1\") { ...P } } fragment P on Post { id }");
        assert!(message.contains("can never apply to 'User'"), "unexpected message: {message}");
    }

    #[test]
    fn an_inline_fragment_on_an_unrelated_type_is_rejected() {
        let message = error_message("query { user(id: \"1\") { ... on Post { id } } }");
        assert!(message.contains("can never apply to 'User'"), "unexpected message: {message}");
    }

    #[test]
    fn a_fragment_on_the_same_type_is_possible() {
        validate("query { user(id: \"1\") { ...U } } fragment U on User { name }")
            .expect("a fragment on its own parent type is always possible");
    }

    // --- Uniqueness -----------------------------------------------------------------------------

    #[test]
    fn a_repeated_argument_on_one_field_is_rejected() {
        let message = error_message("query { user(id: \"1\", id: \"2\") { name } }");
        assert!(message.contains("provided more than once"), "unexpected message: {message}");
    }

    #[test]
    fn a_repeated_variable_declaration_is_rejected() {
        let message = error_message("query Q($id: ID!, $id: ID!) { user(id: $id) { name } }");
        assert!(
            message.contains("'$id' is declared more than once"),
            "unexpected message: {message}"
        );
    }

    // --- Variable usage types ---------------------------------------------------------------

    #[test]
    fn a_variable_of_the_wrong_type_is_rejected() {
        let message = error_message("query Q($id: Int!) { user(id: $id) { name } }");
        assert!(message.contains("is declared as 'Int!'"), "unexpected message: {message}");
    }

    #[test]
    fn a_variable_the_operation_never_declared_is_rejected() {
        let message = error_message("query Q { user(id: $missing) { name } }");
        assert!(
            message.contains("undefined variable '$missing'"),
            "unexpected message: {message}"
        );
    }

    #[test]
    fn a_correctly_typed_variable_is_accepted() {
        validate("query Q($id: ID!) { user(id: $id) { name } }").expect("a matching variable is valid");
    }

    #[test]
    fn a_non_null_variable_is_allowed_where_a_nullable_value_is_wanted() {
        // `search(limit: Int!)` has a default, so it accepts an omitted or non-null value.
        validate("query Q($limit: Int!) { search(limit: $limit) { name } }").expect("non-null into non-null is valid");
    }

    #[test]
    fn a_nullable_variable_is_allowed_in_a_non_null_position_that_has_a_default() {
        // The spec's `hasLocationDefaultValue` half of `IsVariableUsageAllowed`. Omitting it
        // rejects the perfectly ordinary `search(limit: Int! = 10)` called with `$limit: Int`.
        validate("query Q($limit: Int) { search(limit: $limit) { name } }")
            .expect("a location default permits a nullable variable");
    }

    #[test]
    fn a_nullable_variable_is_rejected_in_a_non_null_position_with_no_default() {
        // `user(id: ID!)` declares no default, so nothing guarantees a value would be present.
        let message = error_message("query Q($id: ID) { user(id: $id) { name } }");
        assert!(message.contains("is declared as 'ID'"), "unexpected message: {message}");
    }

    #[test]
    fn a_variable_with_its_own_default_may_be_used_in_a_non_null_position() {
        validate("query Q($id: ID = \"1\") { user(id: $id) { name } }").expect("a variable default guarantees a value");
    }

    #[test]
    fn a_list_variable_must_match_the_expected_nesting() {
        let message = error_message("query Q($limit: [Int!]!) { search(limit: $limit) { name } }");
        assert!(message.contains("is declared as '[Int!]!'"), "unexpected message: {message}");
    }

    #[test]
    fn distinct_arguments_and_variables_still_validate() {
        validate("query Q($id: ID!, $limit: Int) { user(id: $id) { name } search(limit: $limit) { name } }")
            .expect("distinct names are valid");
    }

    // --- Subscription single root field ---------------------------------------------------------

    fn subscription_schema() -> CompiledSchema {
        let mut schema = test_schema();
        schema.subscription_type_name = Some("Subscription".to_string());
        schema.types.insert(
            "Subscription".to_string(),
            object(
                "Subscription",
                vec![
                    field("ticks", named("User"), Vec::new()),
                    field("pings", named("User"), Vec::new()),
                ],
            ),
        );
        schema
    }

    fn validate_subscription(query: &str) -> GraphQLResult<()> {
        let document = parse_document(query).expect("query parses");
        validate_query(&document, &subscription_schema(), None)
    }

    #[test]
    fn a_subscription_with_two_root_fields_is_rejected_at_validation_time() {
        let error = validate_subscription("subscription { ticks { name } pings { name } }")
            .expect_err("two root fields must be rejected");
        assert!(
            error.message.contains("exactly one root field"),
            "unexpected message: {}",
            error.message
        );
    }

    #[test]
    fn a_subscription_whose_single_root_field_arrives_via_a_fragment_is_accepted() {
        validate_subscription("subscription { ...S } fragment S on Subscription { ticks { name } }")
            .expect("one root field through a fragment is valid");
    }

    #[test]
    fn a_subscription_with_two_root_fields_via_a_fragment_is_rejected() {
        let error =
            validate_subscription("subscription { ticks { name } ...S } fragment S on Subscription { pings { name } }")
                .expect_err("two root fields must be rejected however they are spelled");
        assert!(
            error.message.contains("exactly one root field"),
            "unexpected message: {}",
            error.message
        );
    }

    #[test]
    fn a_subscription_root_field_behind_skip_include_is_left_to_execution() {
        // The count depends on variables validation doesn't have, so this must *not* be rejected
        // here -- `subscribe_async`'s own post-pruning check is what decides it.
        validate_subscription("subscription { ticks { name } pings @skip(if: true) { name } }")
            .expect("a conditional root field is not statically decidable");
    }

    #[test]
    fn a_subscription_with_one_root_field_is_accepted() {
        validate_subscription("subscription { ticks { name } }").expect("one root field is valid");
    }
}
