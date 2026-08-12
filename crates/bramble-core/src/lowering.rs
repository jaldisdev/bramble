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

/// A custom operation directive application (§7) surviving lowering -- `@skip`/`@include`/
/// `@defer`/`@stream` never appear here, since they're all structural (pruning, or incremental-
/// delivery marking) decided during lowering itself, not a per-field transform applied after
/// resolution.
#[derive(Debug, Clone, PartialEq)]
pub struct LoweredDirective {
    pub name: String,
    pub arguments: Vec<LoweredArgument>,
}

/// `@defer`'s own marker, carried on a field that came *exclusively* from a `@defer`-applied
/// fragment spread/inline fragment at its enclosing selection set's level -- see
/// `lower_selection_set`'s own doc comment for the exclusivity rule this depends on.
#[derive(Debug, Clone, PartialEq)]
pub struct DeferMarker {
    pub label: Option<String>,
}

/// `@stream`'s own marker, carried on the list-typed field it was directly applied to.
/// `initial_count` is `@stream(initialCount: ...)`'s resolved value (spec default `0`).
#[derive(Debug, Clone, PartialEq)]
pub struct StreamMarker {
    pub initial_count: i64,
    pub label: Option<String>,
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
    /// Set when this field is deliverable only after the initial payload, per `@defer` (§ incremental
    /// delivery). See `lower_selection_set`'s doc comment for the exclusivity rule deciding this.
    pub deferred: Option<DeferMarker>,
    /// Set when this field carries `@stream` directly (only ever legal on a list-typed field --
    /// enforced during validation, not here; lowering just carries the marker through structurally
    /// the same way it does for `deferred`).
    pub streamed: Option<StreamMarker>,
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

fn resolve_int_argument(
    value: &Value,
    variable_values: &HashMap<String, JsonValue>,
    directive_name: &str,
    argument_name: &str,
) -> GraphQLResult<i64> {
    match value {
        Value::Number(number) if number.is_i64() => Ok(number.as_i64().unwrap()),
        Value::Variable(name) => {
            let name = name.as_str();
            match variable_values.get(name) {
                Some(JsonValue::Number(number)) if number.is_i64() => Ok(number.as_i64().unwrap()),
                Some(_) => Err(Box::new(GraphQLError::new(
                    format!("variable '${name}' used in @{directive_name}({argument_name}: ...) must be an integer"),
                    ErrorCode::GraphqlValidationFailed,
                ))),
                None => Err(Box::new(GraphQLError::new(
                    format!("@{directive_name}({argument_name}: ${name}) references undefined variable '${name}'"),
                    ErrorCode::GraphqlValidationFailed,
                ))),
            }
        }
        _ => Err(Box::new(GraphQLError::new(
            format!("@{directive_name}({argument_name}: ...) argument must be an integer or a variable"),
            ErrorCode::GraphqlValidationFailed,
        ))),
    }
}

fn resolve_label_argument(value: &Value, directive_name: &str) -> GraphQLResult<String> {
    match value {
        Value::String(label) => Ok(label.clone()),
        _ => Err(Box::new(GraphQLError::new(
            format!("@{directive_name}(label: ...) must be a static string, not a variable"),
            ErrorCode::GraphqlValidationFailed,
        ))),
    }
}

/// Evaluates a fragment spread/inline fragment's own `@defer` directive (if any): `Ok(None)` if
/// absent, or present with `if: false`; `Ok(Some(label))` if actively deferred (`label` is
/// `@defer`'s own optional `label: "..."` argument -- must be a static string per spec, never a
/// variable, since the client needs it to identify which patch a label belongs to without waiting
/// for the operation to actually execute).
fn defer_label(
    directives: &[Positioned<async_graphql_parser::types::Directive>],
    variable_values: &HashMap<String, JsonValue>,
) -> GraphQLResult<Option<Option<String>>> {
    for directive in directives {
        if directive.node.name.node.as_str() != "defer" {
            continue;
        }

        let if_argument = directive
            .node
            .arguments
            .iter()
            .find(|(name, _)| name.node.as_str() == "if")
            .map(|(_, value)| &value.node);
        let active = match if_argument {
            Some(value) => resolve_boolean_argument(value, variable_values, "defer")?,
            None => true,
        };
        if !active {
            return Ok(None);
        }

        let label = directive
            .node
            .arguments
            .iter()
            .find(|(name, _)| name.node.as_str() == "label")
            .map(|(_, value)| resolve_label_argument(&value.node, "defer"))
            .transpose()?;
        return Ok(Some(label));
    }
    Ok(None)
}

/// Evaluates a field's own `@stream` directive (if any) -- mirrors `defer_label` but for
/// `@stream`'s own argument shape (`initialCount: Int = 0` in addition to `if`/`label`).
fn stream_marker(
    directives: &[Positioned<async_graphql_parser::types::Directive>],
    variable_values: &HashMap<String, JsonValue>,
) -> GraphQLResult<Option<StreamMarker>> {
    for directive in directives {
        if directive.node.name.node.as_str() != "stream" {
            continue;
        }

        let if_argument = directive
            .node
            .arguments
            .iter()
            .find(|(name, _)| name.node.as_str() == "if")
            .map(|(_, value)| &value.node);
        let active = match if_argument {
            Some(value) => resolve_boolean_argument(value, variable_values, "stream")?,
            None => true,
        };
        if !active {
            return Ok(None);
        }

        let initial_count = directive
            .node
            .arguments
            .iter()
            .find(|(name, _)| name.node.as_str() == "initialCount")
            .map(|(_, value)| resolve_int_argument(&value.node, variable_values, "stream", "initialCount"))
            .transpose()?
            .unwrap_or(0);
        let label = directive
            .node
            .arguments
            .iter()
            .find(|(name, _)| name.node.as_str() == "label")
            .map(|(_, value)| resolve_label_argument(&value.node, "stream"))
            .transpose()?;
        return Ok(Some(StreamMarker { initial_count, label }));
    }
    Ok(None)
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
            name != "skip" && name != "include" && name != "defer" && name != "stream"
        })
        .map(|directive| LoweredDirective {
            name: directive.node.name.node.as_str().to_string(),
            arguments: lower_arguments(&directive.node.arguments),
        })
        .collect()
}

/// Lowers one selection set, applying `@skip`/`@include` pruning and flattening fragment
/// boundaries exactly as before, **plus** deciding which fields (if any) are `@defer`-deliverable.
///
/// A field is only ever marked `deferred` if it came from a `@defer`-applied fragment spread/
/// inline fragment *and* no other selection at this same level also produces that field's own
/// response key -- i.e. it's genuinely exclusive to the deferred fragment. This is a deliberate
/// simplification of the spec's own defer-aware `CollectFields` (which would, among other things,
/// let two *different* deferred fragments both contributing the same response key still defer it,
/// merged under one combined label): a field selected both inside and outside a deferred fragment
/// is needed for the initial payload regardless, so deferring it would be actively wrong; two
/// deferred fragments colliding on the same key is rarer and, for now, simply also falls back to
/// immediate (non-deferred) resolution rather than attempting a real merge. Both cases are a
/// conservative "resolve eagerly instead of deferring" fallback, never silently dropped data.
///
/// `spread_chain` carries the fragment names currently being expanded on this path, guarding the
/// spec's "Fragment spreads must not form cycles" rule. Lowering has to enforce it independently of
/// `validation::validate_query` rather than assuming validation ran first: `_needs_incremental_delivery`
/// (`bramble/http/async_base_view.py`) and the WebSocket handler both lower raw, *unvalidated*
/// client input to peek at the operation type, so an unguarded recursion here is reachable on its
/// own.
fn lower_selection_set(
    selection_set: &SelectionSet,
    fragments: &HashMap<Name, Positioned<FragmentDefinition>>,
    variable_values: &HashMap<String, JsonValue>,
    type_condition: Option<&str>,
    spread_chain: &mut Vec<String>,
) -> GraphQLResult<Vec<LoweredField>> {
    // Each entry's `Option<Option<String>>` mirrors `defer_label`'s own return shape: `None` if
    // this particular occurrence isn't deferred at all, `Some(label)` if it is (with `label`
    // itself possibly absent).
    let mut collected: Vec<(LoweredField, Option<Option<String>>)> = Vec::new();

    for selection in &selection_set.items {
        match &selection.node {
            Selection::Field(field) => {
                if should_prune(&field.node.directives, variable_values)? {
                    continue;
                }
                let response_key = field.node.response_key().node.as_str().to_string();
                let field_name = field.node.name.node.as_str().to_string();
                let selections =
                    lower_selection_set(&field.node.selection_set.node, fragments, variable_values, None, spread_chain)?;
                let streamed = stream_marker(&field.node.directives, variable_values)?;
                collected.push((
                    LoweredField {
                        response_key,
                        field_name,
                        type_condition: type_condition.map(str::to_string),
                        arguments: lower_arguments(&field.node.arguments),
                        directives: lower_directives(&field.node.directives),
                        selections,
                        location: Location::from(field.pos),
                        deferred: None,
                        streamed,
                    },
                    None,
                ));
            }
            Selection::InlineFragment(inline) => {
                if should_prune(&inline.node.directives, variable_values)? {
                    continue;
                }
                let nested_condition = inline
                    .node
                    .type_condition
                    .as_ref()
                    .map(|condition| condition.node.on.node.as_str());
                let label = defer_label(&inline.node.directives, variable_values)?;
                let nested = lower_selection_set(
                    &inline.node.selection_set.node,
                    fragments,
                    variable_values,
                    nested_condition.or(type_condition),
                    spread_chain,
                )?;
                collected.extend(nested.into_iter().map(|field| (field, label.clone())));
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
                if spread_chain.iter().any(|seen| seen == fragment_name.as_str()) {
                    return Err(Box::new(GraphQLError::new(
                        format!("Fragment cycle detected involving '{fragment_name}'"),
                        ErrorCode::InvalidFragmentTarget,
                    )));
                }
                // `@defer` is read off the spread itself (`...Foo @defer`), matching where the
                // spec allows it -- never off the `fragment Foo on Type { ... }` definition.
                let label = defer_label(&spread.node.directives, variable_values)?;
                let nested_condition = fragment.node.type_condition.node.on.node.as_str();
                spread_chain.push(fragment_name.to_string());
                let nested = lower_selection_set(
                    &fragment.node.selection_set.node,
                    fragments,
                    variable_values,
                    Some(nested_condition),
                    spread_chain,
                );
                spread_chain.pop();
                collected.extend(nested?.into_iter().map(|field| (field, label.clone())));
            }
        }
    }

    let mut occurrences_by_key: HashMap<String, usize> = HashMap::new();
    for (field, _) in &collected {
        *occurrences_by_key.entry(field.response_key.clone()).or_insert(0) += 1;
    }

    let result = collected
        .into_iter()
        .map(|(mut field, label)| {
            if let Some(label) = label
                && occurrences_by_key[&field.response_key] == 1
            {
                field.deferred = Some(DeferMarker { label });
            }
            field
        })
        .collect();

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
    let fields = lower_selection_set(
        &operation.selection_set.node,
        &document.fragments,
        variable_values,
        None,
        &mut Vec::new(),
    )?;
    Ok((operation.ty, fields))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::parser::parse_document;

    fn lower(query: &str, variable_values: &HashMap<String, JsonValue>) -> Vec<LoweredField> {
        let document = parse_document(query).expect("query parses");
        let (_, fields) = lower_document(&document, variable_values, None).expect("query lowers");
        fields
    }

    fn field<'a>(fields: &'a [LoweredField], response_key: &str) -> &'a LoweredField {
        fields
            .iter()
            .find(|field| field.response_key == response_key)
            .expect("field present")
    }

    fn lowering_error(query: &str) -> String {
        let document = parse_document(query).expect("query parses");
        lower_document(&document, &HashMap::new(), None)
            .expect_err("expected a lowering error")
            .message
    }

    // Lowering enforces the fragment-cycle rule on its own, independently of `validate_query` --
    // the HTTP view's `@defer`/`@stream` peek and the WebSocket handler's operation-type peek both
    // lower unvalidated client input, so an unguarded recursion here would hang on its own.
    #[test]
    fn a_self_referencing_fragment_is_rejected_rather_than_looping_forever() {
        let message = lowering_error("query { user { ...A } } fragment A on User { name ...A }");
        assert!(
            message.contains("Fragment cycle detected involving 'A'"),
            "unexpected message: {message}"
        );
    }

    #[test]
    fn a_mutually_recursive_fragment_pair_is_rejected_during_lowering() {
        let message = lowering_error("query { user { ...A } } fragment A on User { ...B } fragment B on User { ...A }");
        assert!(message.contains("Fragment cycle detected"), "unexpected message: {message}");
    }

    #[test]
    fn the_same_fragment_lowered_twice_in_sibling_positions_is_not_a_cycle() {
        let fields = lower(
            "query { user { ...A } other { ...A } } fragment A on User { name }",
            &HashMap::new(),
        );
        assert_eq!(field(&fields, "user").selections.len(), 1);
        assert_eq!(field(&fields, "other").selections.len(), 1);
    }

    #[test]
    fn a_pruned_cyclic_fragment_spread_does_not_trip_the_guard() {
        // `@skip(if: true)` removes the spread structurally before it is ever expanded, so the
        // cycle is never reached -- the guard must not fire on a spread that was pruned.
        let fields = lower(
            "query { user { name ...A @skip(if: true) } } fragment A on User { ...A }",
            &HashMap::new(),
        );
        assert_eq!(field(&fields, "user").selections.len(), 1);
    }

    #[test]
    fn a_field_exclusive_to_a_deferred_fragment_is_marked_deferred() {
        let query = r#"
            query {
                id
                ... @defer(label: "extra") {
                    name
                }
            }
        "#;
        let fields = lower(query, &HashMap::new());

        assert!(field(&fields, "id").deferred.is_none());
        let name = field(&fields, "name");
        assert_eq!(
            name.deferred,
            Some(DeferMarker {
                label: Some("extra".to_string())
            })
        );
    }

    #[test]
    fn a_field_selected_both_inside_and_outside_a_deferred_fragment_is_not_deferred() {
        let query = r#"
            query {
                name
                ... @defer {
                    name
                }
            }
        "#;
        let fields = lower(query, &HashMap::new());

        assert_eq!(fields.len(), 2);
        assert!(fields.iter().all(|field| field.deferred.is_none()));
    }

    #[test]
    fn defer_if_false_does_not_defer() {
        let query = r#"
            query {
                ... @defer(if: false) {
                    name
                }
            }
        "#;
        let fields = lower(query, &HashMap::new());

        assert!(field(&fields, "name").deferred.is_none());
    }

    #[test]
    fn a_deferred_fragment_spread_is_also_recognized() {
        let query = r#"
            query {
                id
                ...Extra @defer
            }
            fragment Extra on Query {
                name
            }
        "#;
        let fields = lower(query, &HashMap::new());

        assert!(field(&fields, "id").deferred.is_none());
        assert_eq!(field(&fields, "name").deferred, Some(DeferMarker { label: None }));
    }

    #[test]
    fn stream_marker_reads_a_variable_supplied_initial_count() {
        let query = r#"
            query($n: Int!) {
                items @stream(initialCount: $n, label: "batch")
            }
        "#;
        let mut variable_values = HashMap::new();
        variable_values.insert("n".to_string(), JsonValue::from(2));
        let fields = lower(query, &variable_values);

        assert_eq!(
            field(&fields, "items").streamed,
            Some(StreamMarker {
                initial_count: 2,
                label: Some("batch".to_string())
            })
        );
    }

    #[test]
    fn stream_initial_count_defaults_to_zero() {
        let fields = lower("query { items @stream }", &HashMap::new());

        assert_eq!(
            field(&fields, "items").streamed,
            Some(StreamMarker {
                initial_count: 0,
                label: None
            })
        );
    }
}
