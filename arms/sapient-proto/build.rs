//! Compile the vendored BSI Flex 335 v2.0 definitions into Rust types.
//!
//! protox rather than protoc: it is a pure-Rust compiler, so nothing but cargo is needed
//! to build this arm. The alternative puts a protobuf toolchain into the builder image
//! and into both CI architectures, to produce bytes that are identical either way.
//!
//! Only the envelope is named. Every other definition arrives as one of its imports, so
//! a file that stops being reachable from `SapientMessage` stops being compiled — which
//! is the correct behaviour, not an omission.

use prost::Message;
use std::path::PathBuf;

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("proto");
    let envelope = "sapient_msg/bsi_flex_335_v2_0/sapient_message.proto";

    // The whole tree, not just the envelope: an edit to any imported definition has to
    // rebuild, or the generated types silently describe the previous wire format.
    println!("cargo:rerun-if-changed={}", root.display());

    let fds = protox::compile([envelope], [&root])?;
    let out = PathBuf::from(std::env::var("OUT_DIR")?);
    std::fs::write(out.join("sapient_descriptor.bin"), fds.encode_to_vec())?;
    prost_build::Config::new().compile_fds(fds)?;
    Ok(())
}
