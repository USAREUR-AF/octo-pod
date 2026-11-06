//! The SAPIENT session arm's modules, as a library.
//!
//! The `sapient` binary (`src/main.rs`) is the arm and uses these modules unchanged. They
//! are a library so that the fuzz targets in `arms/fuzz` drive the same framing and wire
//! code that reads a peer's bytes, not a copy of it.
//!
//! License: Apache-2.0

pub mod egress;
pub mod framing;
pub mod identity;
pub mod session;
pub mod wire;

// The generated types live in the shared crate; `crate::proto::v2` names them here.
pub use sapient_proto as proto;
