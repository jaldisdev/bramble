/// Converts a snake_case identifier to camelCase (`turn_uppercase` -> `turnUppercase`) -- GraphQL's
/// own field/argument naming convention, and this crate's default when no explicit `name=`
/// override is given (matching Strawberry's own `auto_camel_case` default; `SchemaConfig` can turn
/// it off, in which case the raw Python identifier is used as-is instead of calling this).
#[must_use]
pub fn to_camel_case(name: &str) -> String {
    let mut result = String::with_capacity(name.len());
    let mut capitalize_next = false;
    for ch in name.chars() {
        if ch == '_' {
            capitalize_next = true;
        } else if capitalize_next {
            result.extend(ch.to_uppercase());
            capitalize_next = false;
        } else {
            result.push(ch);
        }
    }
    result
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn converts_snake_case_to_camel_case() {
        assert_eq!(to_camel_case("turn_uppercase"), "turnUppercase");
        assert_eq!(to_camel_case("post_id"), "postId");
        assert_eq!(to_camel_case("id"), "id");
        assert_eq!(to_camel_case("already_camelCase"), "alreadyCamelCase");
    }
}
