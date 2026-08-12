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

use serde::Serialize;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum TypeKind {
    Type,
    Interface,
    Input,
    Enum,
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
    /// The default rendered as a GraphQL literal (`10`, `"abc"`, `RED`, `[1, 2]`), ready to print
    /// after `= ` in SDL and to report as introspection's `__InputValue.defaultValue` (which the
    /// spec defines as a *string* holding the literal, not a typed value). Stored pre-rendered
    /// rather than as a structured value because the Python default is only ever available on the
    /// far side of the PyO3 boundary, and because both consumers want the identical spelling --
    /// deriving it twice would be two chances to disagree.
    ///
    /// `None` while `has_default` is `true` means the default exists but isn't expressible as a
    /// GraphQL literal (an arbitrary object, say). Nothing is rendered in that case: a wrong
    /// literal would be worse than an absent one, and `has_default` still keeps the argument
    /// optional for validation.
    pub default_value: Option<String>,
    pub description: Option<String>,
    pub deprecation_reason: Option<String>,
    /// Declared as `Maybe[T]`: the GraphQL type is a plain nullable `T`, but execution wraps a
    /// supplied value in `Some(...)` so a resolver can tell "provided as null" from "omitted".
    pub is_maybe: bool,
    pub applied_directives: Vec<AppliedDirective>,
}

#[derive(Debug, Clone, Serialize)]
pub struct FieldDefinition {
    pub name: String,
    pub graphql_name: Option<String>,
    pub graphql_type: GraphQLType,
    pub description: Option<String>,
    /// `bramble.field(deprecation_reason=...)` -- rendered as `@deprecated(reason: "...")` in SDL
    /// and reported through `__Field.isDeprecated`/`deprecationReason`.
    pub deprecation_reason: Option<String>,
    /// The field's default rendered as a GraphQL literal, for an **input object** field only --
    /// object and interface fields have no defaults in GraphQL, so this stays `None` for them and
    /// is never rendered even if somehow set. Same representation and same reasoning as
    /// `ArgumentDefinition::default_value`: pre-rendered once on the PyO3 side so SDL and
    /// introspection cannot disagree, and `None` for a default with no faithful literal spelling.
    pub default_value: Option<String>,
    /// Declared as `Maybe[T]` -- see `ArgumentDefinition::is_maybe`.
    pub is_maybe: bool,
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
    /// The GraphQL names of every interface this type (or, for an interface itself, its own
    /// parent interfaces) implements -- §4's `implements A & B` SDL clause. In MRO order, which
    /// is deterministic (Python's C3 linearization) and already deduplicates diamond inheritance.
    pub interfaces: Vec<String>,
    /// This enum's own members, in declaration order -- always empty for a non-`Enum` `kind`. An
    /// enum shares `TypeDefinition` with object/interface/input types (rather than getting its own
    /// IR struct) because every consumer already keyed off `TypeKind`, and a separate struct would
    /// mean a parallel registry in `CompiledSchema` plus a second lookup at every "is this name a
    /// known type" check in validation and SDL rendering.
    pub enum_values: Vec<EnumValueDefinition>,
}

/// One member of a GraphQL enum (§ enums). `name` is the Python member's own identifier
/// (`Color.RED` -> `"RED"`); `graphql_name` overrides what a query actually writes, set via
/// `bramble.enum_value(name=...)`. The Python member's *value* deliberately isn't carried here:
/// it never appears in SDL or on the wire (a GraphQL enum is transmitted purely by member name),
/// and execution resolves it from the live Python class instead.
#[derive(Debug, Clone, Serialize)]
pub struct EnumValueDefinition {
    pub name: String,
    pub graphql_name: Option<String>,
    pub description: Option<String>,
    pub deprecation_reason: Option<String>,
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
    /// Whether this directive's own declaration carries GraphQL's `repeatable` keyword (e.g.
    /// Apollo Federation's `directive @key(...) repeatable on OBJECT | INTERFACE`) -- lets the
    /// same directive be applied more than once to one type/field, which bramble's
    /// `Vec<AppliedDirective>` (§6) already allows at the *application* site regardless of this
    /// flag; this only controls whether the *declaration* advertises that as intentional.
    pub repeatable: bool,
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
    /// The parameter bound to the execution context (`Info`), if any -- mirrors
    /// `FieldDefinition::info_parameter`; a custom operation directive supports `Info` injection
    /// the same way a resolver does (§3c).
    pub info_parameter: Option<String>,
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
    /// Directives applied to the `schema { ... }` block itself (e.g. Apollo Federation's
    /// `schema @link(url: "...") { ... }`) -- distinct from `schema_directives` above, which holds
    /// *declarations* (`directive @name(...) on ...`), not applications.
    pub schema_applied_directives: Vec<AppliedDirective>,
    pub scalar_names: HashSet<String>,
    /// Applied directives (§6) for a registered scalar, keyed by its GraphQL name -- separate
    /// from `scalar_names` (a flat set) since a scalar has no other Rust-side IR of its own to
    /// hang this on the way a type/field does.
    pub scalar_applied_directives: HashMap<String, Vec<AppliedDirective>>,
    /// A registered scalar's own `description=`, keyed by its GraphQL name -- same reasoning as
    /// `scalar_applied_directives` (no other Rust-side IR to carry it on). Absent for a scalar
    /// with no description, not an empty string.
    pub scalar_descriptions: HashMap<String, String>,
    /// `SchemaConfig(auto_camel_case=...)` (default `true`): whether a field/argument with no
    /// explicit `name=` override defaults to a camelCase rendering of its Python identifier
    /// (`post_id` -> `postId`) or the identifier as-is.
    pub auto_camel_case: bool,
    /// A fresh, empty cache per `CompiledSchema` -- constructing a new `Schema()` naturally
    /// flushes it (Task 10's schema-reload decision), since there's nothing to inherit from.
    pub persisted_query_cache: crate::persisted_query::PersistedQueryCache,
}

/// Validates every type's interface conformance against the interfaces it declares, plus that each
/// name a type or union refers to actually resolves (§4/§8b). Runs once, when the schema is
/// compiled -- this is the "Rust owns schema-shape validation" boundary; it used to live in Python
/// (`bramble._schema._validate_interface_implementations`), which meant the compiled schema was
/// assembled without anything checking its shape.
///
/// The conformance checks are deliberately the covariance ones an implementor can still violate by
/// *re-annotating* an inherited field: bramble has implementing types inherit from the interface
/// directly (there is no `implements=[...]` list to get out of sync), so dataclass field
/// inheritance already makes outright omission structurally impossible. This matches the checks
/// graphql-core's own `validate_type_implements_interface` actually performs.
pub fn validate_schema_shape(
    types: &HashMap<String, TypeDefinition>,
    unions: &HashMap<String, UnionDefinition>,
    scalar_names: &HashSet<String>,
) -> Result<(), String> {
    for type_def in types.values() {
        for interface_name in &type_def.interfaces {
            let Some(interface) = types.get(interface_name) else {
                return Err(format!(
                    "'{}' implements interface '{interface_name}', which is not a registered type",
                    type_def.name
                ));
            };
            check_implements(type_def, interface)?;
        }
    }

    for union_def in unions.values() {
        for member_name in &union_def.member_names {
            // A union member must be a real object type. Tolerating a scalar name here would let a
            // union silently include something no `... on Member` selection could ever match.
            if !types.contains_key(member_name) && !scalar_names.contains(member_name) {
                return Err(format!(
                    "union '{}' has member '{member_name}', which is not a registered type",
                    union_def.name
                ));
            }
        }
    }

    Ok(())
}

fn check_implements(implementor: &TypeDefinition, interface: &TypeDefinition) -> Result<(), String> {
    for interface_field in &interface.fields {
        let Some(implementor_field) = implementor.fields.iter().find(|field| field.name == interface_field.name) else {
            return Err(format!(
                "'{}' does not implement field '{}' declared by interface '{}'",
                implementor.name, interface_field.name, interface.name
            ));
        };

        // Widening a non-null interface field to nullable breaks every client that trusted the
        // interface's own contract.
        if !interface_field.graphql_type.is_nullable() && implementor_field.graphql_type.is_nullable() {
            return Err(format!(
                "'{}.{}' is nullable, but interface '{}' declares it as non-null",
                implementor.name, interface_field.name, interface.name
            ));
        }

        for argument in &implementor_field.arguments {
            let declared_by_interface = interface_field.arguments.iter().any(|other| other.name == argument.name);
            // A *newly required* argument is the violation: a client selecting the field through
            // the interface has no way to know it must supply one.
            if !declared_by_interface && !argument.graphql_type.is_nullable() && !argument.has_default {
                return Err(format!(
                    "'{}.{}' adds required argument '{}' not declared by interface '{}'",
                    implementor.name, interface_field.name, argument.name, interface.name
                ));
            }
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn named(name: &str) -> GraphQLType {
        GraphQLType::Named(name.to_string())
    }

    #[test]
    fn to_sdl_string_renders_standard_wrapper_syntax() {
        assert_eq!(named("String").to_sdl_string(), "String");
        assert_eq!(GraphQLType::NonNull(Box::new(named("String"))).to_sdl_string(), "String!");
        assert_eq!(GraphQLType::List(Box::new(named("Int"))).to_sdl_string(), "[Int]");
        assert_eq!(
            GraphQLType::NonNull(Box::new(GraphQLType::List(Box::new(GraphQLType::NonNull(Box::new(named(
                "Int"
            )))))))
            .to_sdl_string(),
            "[Int!]!"
        );
    }

    #[test]
    fn is_nullable_looks_only_at_the_outermost_wrapper() {
        assert!(named("String").is_nullable());
        assert!(GraphQLType::List(Box::new(GraphQLType::NonNull(Box::new(named("Int"))))).is_nullable());
        assert!(!GraphQLType::NonNull(Box::new(named("String"))).is_nullable());
        // A nullable element inside a non-null list does not make the list itself nullable.
        assert!(!GraphQLType::NonNull(Box::new(GraphQLType::List(Box::new(named("Int"))))).is_nullable());
    }

    fn field(name: &str, graphql_type: GraphQLType, arguments: Vec<ArgumentDefinition>) -> FieldDefinition {
        FieldDefinition {
            name: name.to_string(),
            graphql_name: None,
            graphql_type,
            description: None,
            deprecation_reason: None,
            default_value: None,
            is_maybe: false,
            has_resolver: false,
            parent_parameter: None,
            info_parameter: None,
            arguments,
            applied_directives: Vec::new(),
        }
    }

    fn argument(name: &str, graphql_type: GraphQLType, has_default: bool) -> ArgumentDefinition {
        ArgumentDefinition {
            name: name.to_string(),
            graphql_name: None,
            graphql_type,
            has_default,
            default_value: None,
            is_maybe: false,
            description: None,
            deprecation_reason: None,
            applied_directives: Vec::new(),
        }
    }

    fn type_def(name: &str, kind: TypeKind, interfaces: Vec<&str>, fields: Vec<FieldDefinition>) -> TypeDefinition {
        TypeDefinition {
            kind,
            name: name.to_string(),
            description: None,
            one_of: false,
            interfaces: interfaces.into_iter().map(str::to_string).collect(),
            enum_values: Vec::new(),
            fields,
            applied_directives: Vec::new(),
        }
    }

    fn non_null(name: &str) -> GraphQLType {
        GraphQLType::NonNull(Box::new(named(name)))
    }

    fn check(types: Vec<TypeDefinition>, unions: Vec<UnionDefinition>) -> Result<(), String> {
        let types = types.into_iter().map(|type_def| (type_def.name.clone(), type_def)).collect();
        let unions = unions.into_iter().map(|union| (union.name.clone(), union)).collect();
        validate_schema_shape(&types, &unions, &HashSet::new())
    }

    #[test]
    fn a_conforming_implementor_passes() {
        let node = type_def("Node", TypeKind::Interface, vec![], vec![field("id", non_null("ID"), vec![])]);
        let user = type_def(
            "User",
            TypeKind::Type,
            vec!["Node"],
            vec![field("id", non_null("ID"), vec![])],
        );
        check(vec![node, user], vec![]).expect("a conforming implementor is valid");
    }

    #[test]
    fn widening_a_non_null_interface_field_to_nullable_is_rejected() {
        let node = type_def("Node", TypeKind::Interface, vec![], vec![field("id", non_null("ID"), vec![])]);
        let user = type_def("User", TypeKind::Type, vec!["Node"], vec![field("id", named("ID"), vec![])]);
        let error = check(vec![node, user], vec![]).expect_err("nullability widening must be rejected");
        assert!(error.contains("is nullable, but interface"), "unexpected: {error}");
    }

    #[test]
    fn adding_a_required_argument_the_interface_does_not_declare_is_rejected() {
        let node = type_def("Node", TypeKind::Interface, vec![], vec![field("id", non_null("ID"), vec![])]);
        let user = type_def(
            "User",
            TypeKind::Type,
            vec!["Node"],
            vec![field(
                "id",
                non_null("ID"),
                vec![argument("format", non_null("String"), false)],
            )],
        );
        let error = check(vec![node, user], vec![]).expect_err("a new required argument must be rejected");
        assert!(error.contains("adds required argument"), "unexpected: {error}");
    }

    #[test]
    fn adding_an_optional_or_defaulted_argument_is_allowed() {
        let node = type_def("Node", TypeKind::Interface, vec![], vec![field("id", non_null("ID"), vec![])]);
        let nullable_extra = type_def(
            "A",
            TypeKind::Type,
            vec!["Node"],
            vec![field("id", non_null("ID"), vec![argument("format", named("String"), false)])],
        );
        let defaulted_extra = type_def(
            "B",
            TypeKind::Type,
            vec!["Node"],
            vec![field(
                "id",
                non_null("ID"),
                vec![argument("format", non_null("String"), true)],
            )],
        );
        check(vec![node, nullable_extra, defaulted_extra], vec![]).expect("optional additions are allowed");
    }

    #[test]
    fn a_missing_interface_field_is_rejected() {
        let node = type_def("Node", TypeKind::Interface, vec![], vec![field("id", non_null("ID"), vec![])]);
        let user = type_def("User", TypeKind::Type, vec!["Node"], vec![]);
        let error = check(vec![node, user], vec![]).expect_err("a missing field must be rejected");
        assert!(error.contains("does not implement field"), "unexpected: {error}");
    }

    #[test]
    fn implementing_an_unregistered_interface_is_rejected() {
        let user = type_def("User", TypeKind::Type, vec!["Ghost"], vec![]);
        let error = check(vec![user], vec![]).expect_err("an unresolvable interface name must be rejected");
        assert!(error.contains("not a registered type"), "unexpected: {error}");
    }

    #[test]
    fn a_union_member_that_resolves_to_nothing_is_rejected() {
        let union = UnionDefinition {
            name: "Media".to_string(),
            description: None,
            member_names: vec!["Audio".to_string()],
            has_custom_resolve_type: false,
        };
        let error = check(vec![], vec![union]).expect_err("an unresolvable union member must be rejected");
        assert!(error.contains("not a registered type"), "unexpected: {error}");
    }

    #[test]
    fn inner_name_unwraps_every_wrapper_layer() {
        let deep = GraphQLType::NonNull(Box::new(GraphQLType::List(Box::new(GraphQLType::NonNull(Box::new(named(
            "User",
        )))))));
        assert_eq!(deep.inner_name(), "User");
        assert_eq!(named("User").inner_name(), "User");
    }
}
