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
