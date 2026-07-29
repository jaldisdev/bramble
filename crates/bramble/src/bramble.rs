use pyo3::prelude::*;

#[pymodule]
fn _bramble(_module: &Bound<'_, PyModule>) -> PyResult<()> {
    Ok(())
}
