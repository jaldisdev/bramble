use async_graphql_parser::types::ExecutableDocument;

use crate::error::{ErrorCode, GraphQLError, GraphQLResult, Location};

pub fn parse_document(source: &str) -> GraphQLResult<ExecutableDocument> {
    async_graphql_parser::parse_query(source).map_err(|error| {
        let locations = error.positions().map(Location::from).collect();
        Box::new(GraphQLError::new(error.to_string(), ErrorCode::GraphqlParseFailed).with_locations(locations))
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_valid_document() {
        let document = parse_document("{ hello }").unwrap();
        assert!(document.operations.iter().next().is_some());
    }

    #[test]
    fn malformed_document_reports_a_single_parse_error() {
        let error = parse_document("{ hello").unwrap_err();

        assert_eq!(error.extensions.code, ErrorCode::GraphqlParseFailed);
        assert_eq!(
            error.locations,
            Some(vec![Location { line: 1, column: 8 }])
        );
    }
}
