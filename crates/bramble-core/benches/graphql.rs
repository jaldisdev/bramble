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

use std::collections::{HashMap, HashSet};
use std::hint::black_box;

use bramble_core::lowering::lower_document;
use bramble_core::parse_document;
use bramble_core::persisted_query::{PersistedQueryCache, resolve_persisted_query, sha256_hex};
use bramble_core::schema::{
    ArgumentDefinition, CompiledSchema, FieldDefinition, GraphQLType, TypeDefinition, TypeKind,
};
use bramble_core::validation::validate_query;
use criterion::{Criterion, criterion_group, criterion_main};

fn named(name: &str) -> GraphQLType {
    GraphQLType::NonNull(Box::new(GraphQLType::Named(name.to_string())))
}

fn list_of(name: &str) -> GraphQLType {
    GraphQLType::NonNull(Box::new(GraphQLType::List(Box::new(named(name)))))
}

fn field(name: &str, graphql_type: GraphQLType, arguments: Vec<ArgumentDefinition>) -> FieldDefinition {
    FieldDefinition {
        name: name.to_string(),
        graphql_name: None,
        graphql_type,
        description: None,
        has_resolver: !arguments.is_empty(),
        parent_parameter: None,
        info_parameter: None,
        arguments,
        applied_directives: Vec::new(),
    }
}

fn argument(name: &str, graphql_type: GraphQLType) -> ArgumentDefinition {
    ArgumentDefinition {
        name: name.to_string(),
        graphql_name: None,
        graphql_type,
        has_default: false,
        description: None,
        deprecation_reason: None,
        applied_directives: Vec::new(),
    }
}

/// A moderately realistic schema (user/post blog shape) for benchmarking parse/validate/lower at
/// something closer to real query complexity than a single trivial field.
fn sample_schema() -> CompiledSchema {
    let mut types = HashMap::new();

    types.insert(
        "Query".to_string(),
        TypeDefinition {
            kind: TypeKind::Type,
            name: "Query".to_string(),
            description: None,
            one_of: false,
            interfaces: Vec::new(),
            fields: vec![field("user", named("User"), vec![argument("id", named("ID"))])],
            applied_directives: Vec::new(),
        },
    );

    types.insert(
        "User".to_string(),
        TypeDefinition {
            kind: TypeKind::Type,
            name: "User".to_string(),
            description: None,
            one_of: false,
            interfaces: Vec::new(),
            fields: vec![
                field("id", named("ID"), Vec::new()),
                field("name", named("String"), Vec::new()),
                field("email", named("String"), Vec::new()),
                field("posts", list_of("Post"), Vec::new()),
            ],
            applied_directives: Vec::new(),
        },
    );

    types.insert(
        "Post".to_string(),
        TypeDefinition {
            kind: TypeKind::Type,
            name: "Post".to_string(),
            description: None,
            one_of: false,
            interfaces: Vec::new(),
            fields: vec![
                field("id", named("ID"), Vec::new()),
                field("title", named("String"), Vec::new()),
                field("body", named("String"), Vec::new()),
                field("author", named("User"), Vec::new()),
            ],
            applied_directives: Vec::new(),
        },
    );

    CompiledSchema {
        types,
        unions: HashMap::new(),
        query_type_name: "Query".to_string(),
        mutation_type_name: None,
        subscription_type_name: None,
        operation_directives: HashMap::new(),
        schema_directives: HashMap::new(),
        schema_applied_directives: Vec::new(),
        scalar_names: HashSet::new(),
        scalar_applied_directives: HashMap::new(),
        scalar_descriptions: HashMap::new(),
        auto_camel_case: true,
        persisted_query_cache: PersistedQueryCache::new(),
    }
}

const SAMPLE_QUERY: &str = r#"
query GetUser($id: ID!) {
    user(id: $id) {
        id
        name
        email
        posts {
            id
            title
            body
            author {
                id
                name
            }
        }
    }
}
"#;

fn bench_parse(c: &mut Criterion) {
    c.bench_function("parse", |b| {
        b.iter(|| parse_document(black_box(SAMPLE_QUERY)).unwrap());
    });
}

fn bench_validate(c: &mut Criterion) {
    let schema = sample_schema();
    let document = parse_document(SAMPLE_QUERY).unwrap();

    c.bench_function("validate", |b| {
        b.iter(|| validate_query(black_box(&document), black_box(&schema), None).unwrap());
    });
}

fn bench_lower(c: &mut Criterion) {
    let document = parse_document(SAMPLE_QUERY).unwrap();
    let mut variable_values = HashMap::new();
    variable_values.insert("id".to_string(), serde_json::json!("1"));

    c.bench_function("lower", |b| {
        b.iter(|| lower_document(black_box(&document), black_box(&variable_values), None).unwrap());
    });
}

fn bench_end_to_end_without_cache(c: &mut Criterion) {
    let schema = sample_schema();
    let mut variable_values = HashMap::new();
    variable_values.insert("id".to_string(), serde_json::json!("1"));

    c.bench_function("end_to_end_without_cache", |b| {
        b.iter(|| {
            let document = parse_document(black_box(SAMPLE_QUERY)).unwrap();
            validate_query(&document, &schema, None).unwrap();
            lower_document(&document, black_box(&variable_values), None).unwrap();
        });
    });
}

fn bench_end_to_end_with_persisted_query_cache_hit(c: &mut Criterion) {
    let schema = sample_schema();
    let sha256_hash = sha256_hex(SAMPLE_QUERY);
    resolve_persisted_query(&schema, &sha256_hash, Some(SAMPLE_QUERY), None).unwrap();

    let mut variable_values = HashMap::new();
    variable_values.insert("id".to_string(), serde_json::json!("1"));

    c.bench_function("end_to_end_with_persisted_query_cache_hit", |b| {
        b.iter(|| {
            resolve_persisted_query(black_box(&schema), black_box(&sha256_hash), None, None).unwrap();
            let document = schema.persisted_query_cache.get(&sha256_hash).unwrap();
            lower_document(&document, black_box(&variable_values), None).unwrap();
        });
    });
}

criterion_group!(
    benches,
    bench_parse,
    bench_validate,
    bench_lower,
    bench_end_to_end_without_cache,
    bench_end_to_end_with_persisted_query_cache_hit,
);
criterion_main!(benches);
