use pyo3::create_exception;
use pyo3::exceptions::PyException;

create_exception!(
    _bramble,
    GraphQLError,
    PyException,
    "Base exception for spec-shaped GraphQL errors (message/locations/path/extensions). \
     bramble._error.GraphQLError subclasses this in Python with the structured fields; this \
     Rust-defined base is what's shared with errors raised natively during parsing/validation."
);
