// SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
// SPDX-License-Identifier: Apache-2.0
//! `init_tracing` installs the process-wide subscriber, so it gets a test binary of its
//! own: no other test in this process can have installed one first.

#[test]
fn arms_log_at_info_and_above_by_default() {
    std::env::remove_var("RUST_LOG");
    octo_arm_common::init_tracing();
    assert!(tracing::enabled!(tracing::Level::INFO));
    assert!(tracing::enabled!(tracing::Level::WARN));
    assert!(!tracing::enabled!(tracing::Level::DEBUG));
}
