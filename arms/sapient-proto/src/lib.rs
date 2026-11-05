//! The generated SAPIENT types, shared by every crate that speaks SAPIENT.
//!
//! One module per proto package, named as the package is. `v2` is the standard's
//! `sapient_msg.bsi_flex_335_v2_0`; a second ICD version would arrive as a second module
//! beside it, which is why the version is in the path rather than assumed.
//!
//! Nothing here is hand-written. The definitions are vendored under `proto/` and
//! compiled by `build.rs`; see `proto/README.md` for their provenance.
//!
//! License: Apache-2.0

/// BSI Flex 335 v2.0.
pub mod v2 {
    // Generated code, linted as such. The standard's own comments become doc comments —
    // tabs, blank lines and enum names that repeat their prefix and all — so the lints
    // that fire here are about Dstl's formatting, not about anything anyone here can fix.
    // Scoped to this module so the hand-written code in the crates that use it keeps the
    // full set.
    #![allow(clippy::all)]
    include!(concat!(
        env!("OUT_DIR"),
        "/sapient_msg.bsi_flex_335_v2_0.rs"
    ));
}

// The standard's custom options (`sapient_msg.proto`) are compiled because the message
// files import them, but they are not included here: they annotate the definitions rather
// than appearing on the wire, so nothing at runtime reads one. Including them would put a
// type in every binary that exists only to be dead.

/// Descriptor for opt-in reflection and protobuf JSON tooling.
#[cfg(feature = "descriptor")]
pub const FILE_DESCRIPTOR_SET: &[u8] =
    include_bytes!(concat!(env!("OUT_DIR"), "/sapient_descriptor.bin"));
