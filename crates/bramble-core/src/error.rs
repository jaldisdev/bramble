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

use serde::Serialize;

#[derive(Serialize, Debug, Clone, Copy, PartialEq, Eq)]
pub struct Location {
    pub line: usize,
    pub column: usize,
}

impl From<async_graphql_parser::Pos> for Location {
    fn from(pos: async_graphql_parser::Pos) -> Self {
        Self {
            line: pos.line,
            column: pos.column,
        }
    }
}

#[derive(Serialize, Debug, Clone, Copy, PartialEq, Eq)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum ErrorCode {
    GraphqlParseFailed,
    GraphqlValidationFailed,
    InterfaceTypeResolutionFailed,
    UnionTypeResolutionFailed,
    UnknownField,
    UnknownArgument,
    ArgumentTypeMismatch,
    InvalidDirectiveLocation,
    InvalidFragmentTarget,
    PersistedQueryNotFound,
    PersistedQueryMismatch,
    /// Never raised from Rust -- kept in lockstep with `bramble._error.ErrorCode` (§8) so the
    /// executor (pure Python, since it needs live resolved values) can report a resolver
    /// exception using the same typed code space as every other bramble error.
    FieldResolutionFailed,
}

#[derive(Serialize, Debug, Clone)]
pub struct ErrorExtensions {
    pub code: ErrorCode,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub stacktrace: Option<Vec<String>>,
    #[serde(flatten)]
    pub custom: HashMap<String, serde_json::Value>,
}

#[derive(Serialize, Debug, Clone)]
pub struct GraphQLError {
    pub message: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub locations: Option<Vec<Location>>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub path: Option<Vec<serde_json::Value>>,
    pub extensions: ErrorExtensions,
}

/// `GraphQLError` is intentionally not `Copy`-small (it carries an owned message, optional
/// locations/path, and an extensions map) -- boxing it in the `Err` position keeps the `Ok` path
/// (the overwhelmingly common case) from paying for that size in every `Result`.
pub type GraphQLResult<T> = Result<T, Box<GraphQLError>>;

impl GraphQLError {
    pub fn new(message: impl Into<String>, code: ErrorCode) -> Self {
        Self {
            message: message.into(),
            locations: None,
            path: None,
            extensions: ErrorExtensions {
                code,
                stacktrace: None,
                custom: HashMap::new(),
            },
        }
    }

    #[must_use]
    pub fn with_locations(mut self, locations: Vec<Location>) -> Self {
        self.locations = (!locations.is_empty()).then_some(locations);
        self
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn serializes_to_spec_shape() {
        let error =
            GraphQLError::new("boom", ErrorCode::GraphqlParseFailed).with_locations(vec![Location { line: 1, column: 2 }]);

        insta::assert_json_snapshot!(error, @r###"
        {
          "message": "boom",
          "locations": [
            {
              "line": 1,
              "column": 2
            }
          ],
          "extensions": {
            "code": "GRAPHQL_PARSE_FAILED"
          }
        }
        "###);
    }

    #[test]
    fn omits_absent_optional_fields() {
        let error = GraphQLError::new("boom", ErrorCode::GraphqlValidationFailed);

        insta::assert_json_snapshot!(error, @r###"
        {
          "message": "boom",
          "extensions": {
            "code": "GRAPHQL_VALIDATION_FAILED"
          }
        }
        "###);
    }

    #[test]
    fn serializes_multiple_locations_and_a_path() {
        let mut error = GraphQLError::new("field error", ErrorCode::FieldResolutionFailed)
            .with_locations(vec![Location { line: 1, column: 2 }, Location { line: 3, column: 4 }]);
        error.path = Some(vec![
            serde_json::json!("items"),
            serde_json::json!(0),
            serde_json::json!("value"),
        ]);

        insta::assert_json_snapshot!(error, @r###"
        {
          "message": "field error",
          "locations": [
            {
              "line": 1,
              "column": 2
            },
            {
              "line": 3,
              "column": 4
            }
          ],
          "path": [
            "items",
            0,
            "value"
          ],
          "extensions": {
            "code": "FIELD_RESOLUTION_FAILED"
          }
        }
        "###);
    }

    #[test]
    fn serializes_custom_extensions_flattened_alongside_code() {
        let mut error = GraphQLError::new("not found", ErrorCode::UnknownField);
        error.extensions.custom.insert("itemId".to_string(), serde_json::json!("42"));

        insta::assert_json_snapshot!(error, @r###"
        {
          "message": "not found",
          "extensions": {
            "code": "UNKNOWN_FIELD",
            "itemId": "42"
          }
        }
        "###);
    }
}
