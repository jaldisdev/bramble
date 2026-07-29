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
        let error = GraphQLError::new("boom", ErrorCode::GraphqlParseFailed)
            .with_locations(vec![Location { line: 1, column: 2 }]);

        let json = serde_json::to_value(&error).unwrap();

        assert_eq!(
            json,
            serde_json::json!({
                "message": "boom",
                "locations": [{"line": 1, "column": 2}],
                "extensions": {"code": "GRAPHQL_PARSE_FAILED"},
            })
        );
    }

    #[test]
    fn omits_absent_optional_fields() {
        let error = GraphQLError::new("boom", ErrorCode::GraphqlValidationFailed);

        let json = serde_json::to_value(&error).unwrap();

        assert_eq!(
            json,
            serde_json::json!({
                "message": "boom",
                "extensions": {"code": "GRAPHQL_VALIDATION_FAILED"},
            })
        );
    }
}
