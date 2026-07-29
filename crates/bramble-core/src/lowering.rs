use std::collections::HashMap;

use async_graphql_parser::Positioned;
use async_graphql_parser::types::{ExecutableDocument, FragmentDefinition, OperationType, Selection, SelectionSet};
use async_graphql_value::{Name, Value};
use serde_json::Value as JsonValue;

use crate::document::select_operation;
use crate::error::{ErrorCode, GraphQLError, GraphQLResult, Location};

/// A field argument or directive argument as written in the query: the GraphQL-facing name
/// (never a resolver's Python parameter name -- that mapping depends on which concrete type
/// ends up owning the field, only known at execution time for interface/union fields, so it
/// isn't resolved here) paired with its literal value. `value` may still be `Value::Variable`;
/// substituting it against the request's variable values is left to the caller (bramble-core has
/// no notion of a Python value to substitute in).
#[derive(Debug, Clone, PartialEq)]
pub struct LoweredArgument {
    pub graphql_name: String,
    pub value: Value,
}

fn lower_arguments(arguments: &[(Positioned<Name>, Positioned<Value>)]) -> Vec<LoweredArgument> {
    arguments
        .iter()
        .map(|(name, value)| LoweredArgument {
            graphql_name: name.node.as_str().to_string(),
            value: value.node.clone(),
        })
        .collect()
}

/// A custom operation directive application (§7) surviving lowering -- `@skip`/`@include` never
/// appear here, since they're structural pruning decided during lowering itself, not a per-field
/// transform applied after resolution.
#[derive(Debug, Clone, PartialEq)]
pub struct LoweredDirective {
    pub name: String,
    pub arguments: Vec<LoweredArgument>,
}

/// A field that survived `@skip`/`@include` pruning, with fragment spreads and inline fragments
/// flattened into their parent's selection list -- **except** for the type narrowing they may
/// introduce, which is preserved via `type_condition` rather than discarded (unlike the plain
/// structural pruning this replaces): a field nested under `... on Circle { ... }` needs its
/// `type_condition` checked against the concrete resolved type at execution time (§4/§5), since
/// different implementors of an interface (or members of a union) can have different fields.
/// Plain object-type fields (the common case) simply carry `type_condition: None`, meaning
/// "applies regardless of concrete type."
#[derive(Debug, Clone, PartialEq)]
pub struct LoweredField {
    /// The output dict key: the query's alias if it declared one, else `field_name`.
    pub response_key: String,
    /// The schema field name actually being requested (never the alias).
    pub field_name: String,
    /// The schema type name this selection is scoped to, from the nearest enclosing inline
    /// fragment or fragment spread (if any) -- `None` if this field wasn't reached through one.
    pub type_condition: Option<String>,
    pub arguments: Vec<LoweredArgument>,
    pub directives: Vec<LoweredDirective>,
    pub selections: Vec<LoweredField>,
    /// This field's own source position in the query text -- lets an execution-time error (a
    /// resolver exception, a "cannot return null for non-null field") report `locations` the same
    /// way a parse/validation error already does, instead of only ever having `path`.
    pub location: Location,
}

fn resolve_boolean_argument(
    value: &Value,
    variable_values: &HashMap<String, JsonValue>,
    directive_name: &str,
) -> GraphQLResult<bool> {
    match value {
        Value::Boolean(condition) => Ok(*condition),
        Value::Variable(name) => {
            let name = name.as_str();
            match variable_values.get(name) {
                Some(JsonValue::Bool(condition)) => Ok(*condition),
                Some(_) => Err(Box::new(GraphQLError::new(
                    format!("variable '${name}' used in @{directive_name}(if: ...) must be a boolean"),
                    ErrorCode::GraphqlValidationFailed,
                ))),
                None => Err(Box::new(GraphQLError::new(
                    format!("@{directive_name}(if: ${name}) references undefined variable '${name}'"),
                    ErrorCode::GraphqlValidationFailed,
                ))),
            }
        }
        _ => Err(Box::new(GraphQLError::new(
            format!("@{directive_name}(if: ...) argument must be a boolean or a variable"),
            ErrorCode::GraphqlValidationFailed,
        ))),
    }
}

/// Evaluates a selection's own `@skip`/`@include` directives (if any), per the spec's combined
/// semantics: prune if `@skip(if: true)` or `@include(if: false)`.
fn should_prune(
    directives: &[Positioned<async_graphql_parser::types::Directive>],
    variable_values: &HashMap<String, JsonValue>,
) -> GraphQLResult<bool> {
    for directive in directives {
        let name = directive.node.name.node.as_str();
        if name != "skip" && name != "include" {
            continue;
        }

        let if_argument = directive
            .node
            .arguments
            .iter()
            .find(|(arg_name, _)| arg_name.node.as_str() == "if")
            .map(|(_, value)| &value.node)
            .ok_or_else(|| {
                Box::new(GraphQLError::new(
                    format!("@{name} requires an 'if' argument"),
                    ErrorCode::GraphqlValidationFailed,
                ))
            })?;

        let condition = resolve_boolean_argument(if_argument, variable_values, name)?;
        if (name == "skip" && condition) || (name == "include" && !condition) {
            return Ok(true);
        }
    }
    Ok(false)
}

/// The custom (non-`skip`/`include`) directives on a selection, carried through to execution
/// as-is -- their arguments aren't resolved against `variable_values` here (unlike `@skip`/
/// `@include`'s own `if` argument just above) since bramble-core has no Python value to produce;
/// that substitution happens once these reach the PyO3 boundary.
fn lower_directives(directives: &[Positioned<async_graphql_parser::types::Directive>]) -> Vec<LoweredDirective> {
    directives
        .iter()
        .filter(|directive| {
            let name = directive.node.name.node.as_str();
            name != "skip" && name != "include"
        })
        .map(|directive| LoweredDirective {
            name: directive.node.name.node.as_str().to_string(),
            arguments: lower_arguments(&directive.node.arguments),
        })
        .collect()
}

fn lower_selection_set(
    selection_set: &SelectionSet,
    fragments: &HashMap<Name, Positioned<FragmentDefinition>>,
    variable_values: &HashMap<String, JsonValue>,
    type_condition: Option<&str>,
) -> GraphQLResult<Vec<LoweredField>> {
    let mut result = Vec::new();

    for selection in &selection_set.items {
        match &selection.node {
            Selection::Field(field) => {
                if should_prune(&field.node.directives, variable_values)? {
                    continue;
                }
                let response_key = field.node.response_key().node.as_str().to_string();
                let field_name = field.node.name.node.as_str().to_string();
                let selections =
                    lower_selection_set(&field.node.selection_set.node, fragments, variable_values, None)?;
                result.push(LoweredField {
                    response_key,
                    field_name,
                    type_condition: type_condition.map(str::to_string),
                    arguments: lower_arguments(&field.node.arguments),
                    directives: lower_directives(&field.node.directives),
                    selections,
                    location: Location::from(field.pos),
                });
            }
            Selection::InlineFragment(inline) => {
                if should_prune(&inline.node.directives, variable_values)? {
                    continue;
                }
                let nested_condition = inline.node.type_condition.as_ref().map(|condition| condition.node.on.node.as_str());
                let nested = lower_selection_set(
                    &inline.node.selection_set.node,
                    fragments,
                    variable_values,
                    nested_condition.or(type_condition),
                )?;
                result.extend(nested);
            }
            Selection::FragmentSpread(spread) => {
                if should_prune(&spread.node.directives, variable_values)? {
                    continue;
                }
                let fragment_name = &spread.node.fragment_name.node;
                let fragment = fragments.get(fragment_name).ok_or_else(|| {
                    Box::new(GraphQLError::new(
                        format!("undefined fragment '{fragment_name}'"),
                        ErrorCode::GraphqlValidationFailed,
                    ))
                })?;
                if should_prune(&fragment.node.directives, variable_values)? {
                    continue;
                }
                let nested_condition = fragment.node.type_condition.node.on.node.as_str();
                let nested = lower_selection_set(
                    &fragment.node.selection_set.node,
                    fragments,
                    variable_values,
                    Some(nested_condition),
                )?;
                result.extend(nested);
            }
        }
    }

    Ok(result)
}

/// Renders an `OperationType` the way the execution bridge needs it: a plain lowercase string
/// picking `query`/`mutation`/`subscription` as the schema's root type, matching GraphQL's own
/// keyword spelling for each.
#[must_use]
pub fn operation_type_str(operation_type: OperationType) -> &'static str {
    match operation_type {
        OperationType::Query => "query",
        OperationType::Mutation => "mutation",
        OperationType::Subscription => "subscription",
    }
}

/// Lowers a parsed document's chosen operation into a `LoweredField` tree: `@skip`/`@include`d
/// selections pruned, fragment spreads/inline fragments flattened (their type conditions
/// preserved on the fields they contain), evaluating each directive's `if` argument against
/// `variable_values` (a literal boolean, or a `$variable` looked up here). This is intentionally
/// schema-agnostic -- like the pruning pass it replaces, it only needs the query document itself,
/// since matching a field's `type_condition` against a concrete resolved type, and binding its
/// arguments to a resolver's actual parameters, both need live schema/domain knowledge that only
/// exists once execution (not lowering) reaches that field. Also returns the operation's own
/// `OperationType` -- execution needs it to pick `query`/`mutation`/`subscription` as the root
/// type before it can resolve a single field.
pub fn lower_document(
    document: &ExecutableDocument,
    variable_values: &HashMap<String, JsonValue>,
    operation_name: Option<&str>,
) -> GraphQLResult<(OperationType, Vec<LoweredField>)> {
    let operation = select_operation(document, operation_name)?;
    let fields = lower_selection_set(&operation.selection_set.node, &document.fragments, variable_values, None)?;
    Ok((operation.ty, fields))
}
