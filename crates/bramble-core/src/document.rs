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
