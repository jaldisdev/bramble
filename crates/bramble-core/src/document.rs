use async_graphql_parser::types::{ExecutableDocument, OperationDefinition};
use async_graphql_value::Name;

use crate::error::{ErrorCode, GraphQLError, GraphQLResult};

/// Picks the operation to act on: the named one if `operation_name` is given, or the document's
/// sole operation if it has exactly one. A document with multiple operations and no
/// `operation_name` is ambiguous -- matches real GraphQL request semantics (a request always
/// targets exactly one operation).
pub fn select_operation<'a>(
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
