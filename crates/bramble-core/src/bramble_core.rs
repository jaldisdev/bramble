pub mod document;
pub mod error;
pub mod lowering;
pub mod persisted_query;
pub mod schema;
pub mod validation;

mod parser;

pub use parser::parse_document;
