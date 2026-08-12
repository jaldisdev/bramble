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

use async_graphql_parser::types::{ExecutableDocument, Selection, SelectionSet};
use pyo3::prelude::*;

use crate::error::raise;

/// A variable definition inside an operation's own `(...)` parameter list, e.g. `$slug: String!`
/// -- exposes the type exactly as written in the query (`type_str`), not resolved against any
/// schema, since codegen (the only consumer of this module) works from the query text alone.
#[pyclass(name = "QueryVariableDefinition", frozen, get_all, skip_from_py_object)]
pub struct PyQueryVariableDefinition {
    pub name: String,
    pub type_str: String,
    pub has_default: bool,
}

/// One selection inside a selection set: a field, a named fragment spread, or an inline
/// fragment -- distinguished by `kind`, with only the fields relevant to that kind populated
/// (mirrors how `bramble_core`'s own `Selection` enum works, just flattened into one Python-facing
/// shape rather than a tagged union, since PyO3 classes don't nest as cleanly as a Rust enum).
/// Fragment spreads are **not** flattened here (unlike `bramble_core::lowering::LoweredField`,
/// which flattens them for execution's sake) -- codegen wants each named fragment as its own
/// reusable unit, so `fragment_name` is left for the caller to look up in the document's own
/// `fragments` list.
#[pyclass(name = "QuerySelection", frozen, get_all, skip_from_py_object)]
pub struct PyQuerySelection {
    pub kind: String,
    pub field_name: Option<String>,
    pub alias: Option<String>,
    pub fragment_name: Option<String>,
    pub type_condition: Option<String>,
    pub directive_names: Vec<String>,
    pub selections: Vec<Py<PyQuerySelection>>,
}

#[pyclass(name = "QueryOperation", frozen, get_all, skip_from_py_object)]
pub struct PyQueryOperation {
    pub operation_type: String,
    pub name: Option<String>,
    pub variables: Vec<Py<PyQueryVariableDefinition>>,
    pub selections: Vec<Py<PyQuerySelection>>,
}

#[pyclass(name = "QueryFragment", frozen, get_all, skip_from_py_object)]
pub struct PyQueryFragment {
    pub name: String,
    pub type_condition: String,
    pub selections: Vec<Py<PyQuerySelection>>,
}

#[pyclass(name = "QueryDocument", frozen, get_all, skip_from_py_object)]
pub struct PyQueryDocument {
    pub operations: Vec<Py<PyQueryOperation>>,
    pub fragments: Vec<Py<PyQueryFragment>>,
}

fn convert_selection_set(py: Python<'_>, selection_set: &SelectionSet) -> PyResult<Vec<Py<PyQuerySelection>>> {
    selection_set
        .items
        .iter()
        .map(|selection| {
            let converted = match &selection.node {
                Selection::Field(field) => {
                    let field = &field.node;
                    PyQuerySelection {
                        kind: "field".to_string(),
                        field_name: Some(field.name.node.as_str().to_string()),
                        alias: field.alias.as_ref().map(|alias| alias.node.as_str().to_string()),
                        fragment_name: None,
                        type_condition: None,
                        directive_names: field
                            .directives
                            .iter()
                            .map(|directive| directive.node.name.node.as_str().to_string())
                            .collect(),
                        selections: convert_selection_set(py, &field.selection_set.node)?,
                    }
                }
                Selection::FragmentSpread(spread) => {
                    let spread = &spread.node;
                    PyQuerySelection {
                        kind: "fragment_spread".to_string(),
                        field_name: None,
                        alias: None,
                        fragment_name: Some(spread.fragment_name.node.as_str().to_string()),
                        type_condition: None,
                        directive_names: spread
                            .directives
                            .iter()
                            .map(|directive| directive.node.name.node.as_str().to_string())
                            .collect(),
                        selections: Vec::new(),
                    }
                }
                Selection::InlineFragment(inline) => {
                    let inline = &inline.node;
                    PyQuerySelection {
                        kind: "inline_fragment".to_string(),
                        field_name: None,
                        alias: None,
                        fragment_name: None,
                        type_condition: inline
                            .type_condition
                            .as_ref()
                            .map(|condition| condition.node.on.node.as_str().to_string()),
                        directive_names: inline
                            .directives
                            .iter()
                            .map(|directive| directive.node.name.node.as_str().to_string())
                            .collect(),
                        selections: convert_selection_set(py, &inline.selection_set.node)?,
                    }
                }
            };
            Py::new(py, converted)
        })
        .collect()
}

/// Parses `query` into its structural document shape -- operations (each with its own variable
/// definitions and selection set) plus every named fragment definition -- for `bramble.codegen`
/// to walk. Deliberately schema-agnostic (like `lower_query`): resolving a selection's actual
/// GraphQL *type* against a real schema is codegen's own job, done in Python against
/// `schema.types_by_name`, not this parsing step's.
#[pyfunction]
pub fn parse_query_document(py: Python<'_>, query: &str) -> PyResult<PyQueryDocument> {
    let document: ExecutableDocument = bramble_core::parse_document(query).map_err(|error| raise(py, error))?;

    let mut operations = Vec::new();
    for (name, operation) in document.operations.iter() {
        let operation = &operation.node;

        let variables = operation
            .variable_definitions
            .iter()
            .map(|variable| {
                let variable = &variable.node;
                Py::new(
                    py,
                    PyQueryVariableDefinition {
                        name: variable.name.node.as_str().to_string(),
                        type_str: variable.var_type.node.to_string(),
                        has_default: variable.default_value.is_some(),
                    },
                )
            })
            .collect::<PyResult<Vec<_>>>()?;

        operations.push(Py::new(
            py,
            PyQueryOperation {
                operation_type: bramble_core::lowering::operation_type_str(operation.ty).to_string(),
                name: name.map(|name| name.as_str().to_string()),
                variables,
                selections: convert_selection_set(py, &operation.selection_set.node)?,
            },
        )?);
    }

    let mut fragments = Vec::new();
    for (name, fragment) in document.fragments.iter() {
        let fragment = &fragment.node;
        fragments.push(Py::new(
            py,
            PyQueryFragment {
                name: name.as_str().to_string(),
                type_condition: fragment.type_condition.node.on.node.as_str().to_string(),
                selections: convert_selection_set(py, &fragment.selection_set.node)?,
            },
        )?);
    }

    Ok(PyQueryDocument { operations, fragments })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_operations_variables_and_selections() {
        Python::attach(|py| {
            let document = parse_query_document(
                py,
                "query GetUser($id: ID!, $n: Int = 3) { user(id: $id) { name posts { id } } }",
            )
            .unwrap();

            assert_eq!(document.operations.len(), 1);
            let operation = document.operations[0].bind(py).borrow();
            assert_eq!(operation.operation_type, "query");
            assert_eq!(operation.name.as_deref(), Some("GetUser"));

            let variables: Vec<(String, String, bool)> = operation
                .variables
                .iter()
                .map(|variable| {
                    let variable = variable.bind(py).borrow();
                    (variable.name.clone(), variable.type_str.clone(), variable.has_default)
                })
                .collect();
            assert_eq!(
                variables,
                vec![
                    ("id".to_string(), "ID!".to_string(), false),
                    ("n".to_string(), "Int".to_string(), true),
                ]
            );

            let user = operation.selections[0].bind(py).borrow();
            assert_eq!(user.kind, "field");
            assert_eq!(user.field_name.as_deref(), Some("user"));
            assert_eq!(user.selections.len(), 2, "nested selections are walked");
        });
    }

    #[test]
    fn an_alias_is_preserved_separately_from_the_field_name() {
        Python::attach(|py| {
            let document = parse_query_document(py, "{ renamed: user { name } }").unwrap();
            let operation = document.operations[0].bind(py).borrow();
            let field = operation.selections[0].bind(py).borrow();

            assert_eq!(field.field_name.as_deref(), Some("user"));
            assert_eq!(field.alias.as_deref(), Some("renamed"));
        });
    }

    #[test]
    fn fragment_spreads_stay_unflattened_for_codegen() {
        // Unlike execution's lowering, codegen wants each named fragment as its own reusable unit.
        Python::attach(|py| {
            let document = parse_query_document(py, "{ user { ...Details } } fragment Details on User { name }").unwrap();
            let operation = document.operations[0].bind(py).borrow();
            let user = operation.selections[0].bind(py).borrow();
            let spread = user.selections[0].bind(py).borrow();

            assert_eq!(spread.kind, "fragment_spread");
            assert_eq!(spread.fragment_name.as_deref(), Some("Details"));
            assert!(spread.selections.is_empty(), "a spread is not expanded in place");

            assert_eq!(document.fragments.len(), 1);
            let fragment = document.fragments[0].bind(py).borrow();
            assert_eq!(fragment.name, "Details");
            assert_eq!(fragment.type_condition, "User");
        });
    }

    #[test]
    fn an_inline_fragment_records_its_type_condition() {
        Python::attach(|py| {
            let document = parse_query_document(py, "{ node { ... on Dog { name } } }").unwrap();
            let operation = document.operations[0].bind(py).borrow();
            let node = operation.selections[0].bind(py).borrow();
            let inline = node.selections[0].bind(py).borrow();

            assert_eq!(inline.kind, "inline_fragment");
            assert_eq!(inline.type_condition.as_deref(), Some("Dog"));
        });
    }

    #[test]
    fn directive_names_are_recorded_on_a_selection() {
        Python::attach(|py| {
            let document = parse_query_document(py, "{ user @include(if: true) { name } }").unwrap();
            let operation = document.operations[0].bind(py).borrow();
            let field = operation.selections[0].bind(py).borrow();

            assert_eq!(field.directive_names, vec!["include".to_string()]);
        });
    }

    #[test]
    fn a_malformed_document_surfaces_as_an_error_not_a_panic() {
        Python::attach(|py| {
            assert!(parse_query_document(py, "{ user(").is_err());
        });
    }
}
