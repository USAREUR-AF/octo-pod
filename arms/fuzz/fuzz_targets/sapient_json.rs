//! The SAPIENT test receiver's protobuf JSON decoder (`sapient_receiver::json::decode`)
//! and the reference-rule validator behind it (`validate::violations`).
//!
//! JSON does not arrive on the receiver's socket: the decoder reads the reference vectors
//! and test fixtures. The target starts from those vectors, so mutations stay close to
//! well-formed messages and reach the validator's deeper rules, which random protobuf
//! bytes rarely do.

#![no_main]

use libfuzzer_sys::fuzz_target;
use sapient_receiver::{json, validate};

fuzz_target!(|data: &[u8]| {
    let Ok(text) = std::str::from_utf8(data) else {
        return;
    };
    let Ok(msg) = json::decode(text) else {
        return;
    };
    let _ = validate::violations(&msg);
});
