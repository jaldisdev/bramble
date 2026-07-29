use serde::Serialize;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum TypeKind {
    Type,
    Interface,
    Input,
}

#[derive(Debug, Clone, Serialize)]
pub struct ArgumentDefinition {
    pub name: String,
    pub graphql_name: Option<String>,
    pub type_repr: Option<String>,
    pub is_nullable: bool,
    pub has_default: bool,
    pub description: Option<String>,
    pub deprecation_reason: Option<String>,
}

#[derive(Debug, Clone, Serialize)]
pub struct FieldDefinition {
    pub name: String,
    pub type_repr: Option<String>,
    pub is_nullable: bool,
    pub has_resolver: bool,
    /// The resolver parameter bound to the parent/root value (`Parent[T]`), if any.
    pub parent_parameter: Option<String>,
    /// The resolver parameter bound to the execution context (`Info`), if any.
    pub info_parameter: Option<String>,
    /// The resolver's remaining parameters, each a GraphQL field argument.
    pub arguments: Vec<ArgumentDefinition>,
}

#[derive(Debug, Clone, Serialize)]
pub struct TypeDefinition {
    pub kind: TypeKind,
    pub name: String,
    pub description: Option<String>,
    pub one_of: bool,
    pub fields: Vec<FieldDefinition>,
}

#[derive(Debug, Clone, Serialize)]
pub struct UnionDefinition {
    pub name: String,
    pub description: Option<String>,
    pub member_type_reprs: Vec<String>,
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
    pub type_repr: Option<String>,
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
