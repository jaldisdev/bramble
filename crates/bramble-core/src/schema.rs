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
