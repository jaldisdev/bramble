use std::collections::HashMap;

use async_graphql_parser::Positioned;
use async_graphql_parser::types::{
    ExecutableDocument, FragmentDefinition, OperationDefinition, Selection, SelectionSet,
};
use async_graphql_value::{Name, Value};
use serde_json::Value as JsonValue;

use crate::error::{ErrorCode, GraphQLError, GraphQLResult};

/// A field that survived `@skip`/`@include` pruning, with fragment spreads and inline fragments
/// already flattened into their parent's selection list -- pruning is orthogonal to type-
/// condition narrowing (which needs a schema, Task 9's job), and GraphQL execution semantics
/// treat a fragment's fields as if they were directly present in the parent selection anyway.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PrunedField {
    pub name: String,
    pub selections: Vec<PrunedField>,
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
/// semantics: prune if `@skip(if: true)` or `@include(if: false)`. Other directives (custom
/// operation directives, per §7's second mechanism) are ignored here entirely -- those apply to
/// already-resolved values during execution, not to structural pruning.
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

fn prune_selection_set(
    selection_set: &SelectionSet,
    fragments: &HashMap<Name, Positioned<FragmentDefinition>>,
    variable_values: &HashMap<String, JsonValue>,
) -> GraphQLResult<Vec<PrunedField>> {
    let mut result = Vec::new();

    for selection in &selection_set.items {
        match &selection.node {
            Selection::Field(field) => {
                if should_prune(&field.node.directives, variable_values)? {
                    continue;
                }
                let name = field.node.response_key().node.as_str().to_string();
                let selections =
                    prune_selection_set(&field.node.selection_set.node, fragments, variable_values)?;
                result.push(PrunedField { name, selections });
            }
            Selection::InlineFragment(inline) => {
                if should_prune(&inline.node.directives, variable_values)? {
                    continue;
                }
                let nested =
                    prune_selection_set(&inline.node.selection_set.node, fragments, variable_values)?;
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
                let nested =
                    prune_selection_set(&fragment.node.selection_set.node, fragments, variable_values)?;
                result.extend(nested);
            }
        }
    }

    Ok(result)
}

fn select_operation<'a>(
    document: &'a ExecutableDocument,
    operation_name: Option<&str>,
) -> GraphQLResult<&'a OperationDefinition> {
    match operation_name {
        Some(target) => document
            .operations
            .iter()
            .find(|(name, _)| name.map(Name::as_str) == Some(target))
            .map(|(_, operation)| &operation.node)
            .ok_or_else(|| {
                Box::new(GraphQLError::new(
                    format!("no operation named '{target}'"),
                    ErrorCode::GraphqlValidationFailed,
                ))
            }),
        None => {
            let operations: Vec<_> = document.operations.iter().collect();
            match operations.as_slice() {
                [(_, operation)] => Ok(&operation.node),
                _ => Err(Box::new(GraphQLError::new(
                    "document contains multiple operations; operation_name is required",
                    ErrorCode::GraphqlValidationFailed,
                ))),
            }
        }
    }
}

/// Prunes `@skip`/`@include`d selections out of a parsed document's chosen operation, evaluating
/// each directive's `if` argument against `variable_values` (a literal boolean, or a `$variable`
/// looked up here). Fragment spreads and inline fragments are flattened into their parent's field
/// list once their own directives (if any) are evaluated.
pub fn prune_document(
    document: &ExecutableDocument,
    variable_values: &HashMap<String, JsonValue>,
    operation_name: Option<&str>,
) -> GraphQLResult<Vec<PrunedField>> {
    let operation = select_operation(document, operation_name)?;
    prune_selection_set(&operation.selection_set.node, &document.fragments, variable_values)
}
