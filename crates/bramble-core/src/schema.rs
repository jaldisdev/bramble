use std::collections::{HashMap, HashSet};

use serde::Serialize;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum TypeKind {
    Type,
    Interface,
    Input,
}

/// A GraphQL type reference: a named type (scalar/object/interface/union/enum/input, by name),
/// optionally wrapped in `List`/`NonNull` -- e.g. `[User!]!` is
/// `NonNull(List(NonNull(Named("User"))))`. Replaces an earlier ad-hoc `type_repr: Option<String>`
/// (just `str(annotation)`, e.g. `"<class 'int'>"`) with a real structure so query validation
/// (Task 9) can check argument/field types against parsed query literals principled-ly rather
/// than by string heuristics.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(tag = "kind", rename_all = "SCREAMING_SNAKE_CASE")]
pub enum GraphQLType {
    Named(String),
    List(Box<GraphQLType>),
    NonNull(Box<GraphQLType>),
}

impl GraphQLType {
    #[must_use]
    pub fn is_nullable(&self) -> bool {
        !matches!(self, GraphQLType::NonNull(_))
    }

    /// The innermost named type's name, unwrapping any `List`/`NonNull` wrappers.
    #[must_use]
    pub fn inner_name(&self) -> &str {
        match self {
            GraphQLType::Named(name) => name,
            GraphQLType::List(inner) | GraphQLType::NonNull(inner) => inner.inner_name(),
        }
    }

    /// Renders standard GraphQL SDL type syntax, e.g. `String!`, `[User]`, `ID`.
    #[must_use]
    pub fn to_sdl_string(&self) -> String {
        match self {
            GraphQLType::Named(name) => name.clone(),
            GraphQLType::List(inner) => format!("[{}]", inner.to_sdl_string()),
            GraphQLType::NonNull(inner) => format!("{}!", inner.to_sdl_string()),
        }
    }
}

#[derive(Debug, Clone, Serialize)]
pub struct ArgumentDefinition {
    pub name: String,
    pub graphql_name: Option<String>,
    pub graphql_type: GraphQLType,
    pub has_default: bool,
    pub description: Option<String>,
    pub deprecation_reason: Option<String>,
}

#[derive(Debug, Clone, Serialize)]
pub struct FieldDefinition {
    pub name: String,
    pub graphql_name: Option<String>,
    pub graphql_type: GraphQLType,
    pub description: Option<String>,
    pub has_resolver: bool,
    /// The resolver parameter bound to the parent/root value (`Parent[T]`), if any.
    pub parent_parameter: Option<String>,
    /// The resolver parameter bound to the execution context (`Info`), if any.
    pub info_parameter: Option<String>,
    /// The resolver's remaining parameters, each a GraphQL field argument.
    pub arguments: Vec<ArgumentDefinition>,
    pub applied_directives: Vec<AppliedDirective>,
}

/// A schema directive instance applied at a specific site (a type, a field, ...; §6) -- the
/// directive's own name plus its field values *at this application*, ready to render as
/// `@name(arg: value, ...)` in SDL. `arguments` is a `Vec` (not a `HashMap`) to preserve
/// declaration order, which matters for reproducible SDL output. Values are `serde_json::Value`
/// rather than `GraphQLType` -- a directive field holds a concrete value here, not a type
/// reference (unlike `ArgumentDefinition`, which describes a field/argument's *type*).
#[derive(Debug, Clone, Serialize)]
pub struct AppliedDirective {
    pub name: String,
    pub arguments: Vec<(String, serde_json::Value)>,
}

#[derive(Debug, Clone, Serialize)]
pub struct TypeDefinition {
    pub kind: TypeKind,
    pub name: String,
    pub description: Option<String>,
    pub one_of: bool,
    pub fields: Vec<FieldDefinition>,
    pub applied_directives: Vec<AppliedDirective>,
}

#[derive(Debug, Clone, Serialize)]
pub struct UnionDefinition {
    pub name: String,
    pub description: Option<String>,
    pub member_names: Vec<String>,
    pub has_custom_resolve_type: bool,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum SchemaDirectiveLocation {
    Schema,
    Scalar,
    Object,
    FieldDefinition,
    ArgumentDefinition,
    Interface,
    Union,
    Enum,
    EnumValue,
    InputObject,
    InputFieldDefinition,
}

#[derive(Debug, Clone, Serialize)]
pub struct DirectiveFieldDefinition {
    pub name: String,
    pub graphql_name: Option<String>,
    pub graphql_type: GraphQLType,
}

#[derive(Debug, Clone, Serialize)]
pub struct SchemaDirectiveDefinition {
    pub name: String,
    pub description: Option<String>,
    pub locations: Vec<SchemaDirectiveLocation>,
    pub fields: Vec<DirectiveFieldDefinition>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum OperationDirectiveLocation {
    Query,
    Mutation,
    Subscription,
    Field,
    FragmentDefinition,
    FragmentSpread,
    InlineFragment,
}

#[derive(Debug, Clone, Serialize)]
pub struct OperationDirectiveDefinition {
    pub name: String,
    pub description: Option<String>,
    pub locations: Vec<OperationDirectiveLocation>,
    /// The parameter bound to the field's already-resolved value (`DirectiveValue[T]`), if any.
    pub value_parameter: Option<String>,
    /// The directive's remaining parameters, each an argument supplied at the directive's use
    /// site in the query -- reuses `ArgumentDefinition` since the binding rules (§3a) are
    /// identical to a resolver's own arguments.
    pub arguments: Vec<ArgumentDefinition>,
}

/// The assembled, validated schema (§7b) that query validation (Task 9) and execution (Task 11)
/// operate against for every subsequent request -- built once, by `Schema()`'s Python-side graph
/// walker (Task 8b) handing over everything it already discovered, not re-derived here.
#[derive(Clone)]
pub struct CompiledSchema {
    pub types: HashMap<String, TypeDefinition>,
    pub unions: HashMap<String, UnionDefinition>,
    pub query_type_name: String,
    pub mutation_type_name: Option<String>,
    pub subscription_type_name: Option<String>,
    pub operation_directives: HashMap<String, OperationDirectiveDefinition>,
    pub schema_directives: HashMap<String, SchemaDirectiveDefinition>,
    pub scalar_names: HashSet<String>,
    /// `SchemaConfig(auto_camel_case=...)` (default `true`, matching Strawberry's own default):
    /// whether a field/argument with no explicit `name=` override defaults to a camelCase
    /// rendering of its Python identifier (`post_id` -> `postId`) or the identifier as-is.
    pub auto_camel_case: bool,
    /// A fresh, empty cache per `CompiledSchema` -- constructing a new `Schema()` naturally
    /// flushes it (Task 10's schema-reload decision), since there's nothing to inherit from.
    pub persisted_query_cache: crate::persisted_query::PersistedQueryCache,
}
