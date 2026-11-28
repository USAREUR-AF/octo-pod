// SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
// SPDX-License-Identifier: Apache-2.0
//! octo-translate: the translation gateway for one pod's pen.
//!
//! An octo-pod arm captures bytes and does not interpret them (ADR 0001). This gateway is
//! the separate component that ADR names: it holds its own pen certificate, subscribes to
//! the captured keys it was declared with, converts each sample from one declared format
//! to one or more declared formats, and publishes the result in its own namespace.
//!
//! ```text
//!  arm (capture)          pen (pod-local)              octo-translate
//!  raw/<arm>/<feed>/<ip>  ACL: read on the declared    decode -> shared model -> encode
//!  bytes, untouched  -->  source keys only        -->  (goat-transformers, pinned packs)
//!                                                           |
//!  audit sink <-- raw/<id>/<format>/<source key>  <---------+  + attachment: source key,
//!  (as for any publisher)   ACL: write on raw/<id>/**           time and hash; local audit
//! ```
//!
//! The captured sample is never changed, removed or republished: it stays the record. A
//! converted sample is a second, derived publication under another identity, and it
//! names the sample it came from.
//!
//! It maps nothing itself. Field mappings are the packs in `packs/`, read by the
//! goat-transformers engine; this crate holds no field name of any format.
//!
//! What lives where:
//! - [`bundle`]: the pinned pack bundle, checked against its manifest before it is loaded.
//! - [`convert`]: one conversion, and why a sample was not converted.
//! - [`gateway`]: the running gateway: subscribe, queue, convert, publish, count.
//! - [`route`]: the declared subscription, the key a result is published on, and the
//!   check that the two can never form a loop.
//! - [`audit`]: the local record of every sample converted or refused.
//!
//! Failure behavior, the same in every case: a sample that is not converted is counted,
//! recorded with the engine's error, and dropped. The source bytes are never published in
//! place of a result.

pub mod audit;
pub mod bundle;
pub mod convert;
pub mod gateway;
pub mod route;
