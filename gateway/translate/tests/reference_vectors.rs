// SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
// SPDX-License-Identifier: Apache-2.0
//! Every accepted BSI Flex 335 v2.0 reference vector (`arms/sapient-receiver/tests/vectors/true`),
//! as the protobuf bytes a SAPIENT arm captures, through the shipped pack bundle.
//!
//! The vectors are the messages a real SAPIENT network carries: registrations, status
//! reports, tasks, alerts and detections. A gateway declared on a SAPIENT feed receives
//! all of them. This test fixes what happens to each kind, so a pack change that starts
//! publishing a registration as a track, or stops converting detections, fails here.
#![allow(clippy::unwrap_used, clippy::expect_used, clippy::panic)]

use std::collections::BTreeMap;
use std::path::Path;

use goat_transformers::Distribution;
use octo_translate::bundle::Bundle;
use octo_translate::convert::{Outcome, Translator};
use prost::Message;
use sapient_proto::v2::sapient_message::Content;

/// Converted, skipped, and failed by engine error name.
type Tally = (u32, u32, BTreeMap<String, u32>);

fn translator() -> Translator {
    let bundle = Bundle::load(
        &Path::new(env!("CARGO_MANIFEST_DIR")).join("packs"),
        Distribution::Public,
    )
    .unwrap();
    Translator::new(
        bundle,
        "sapient",
        &["cot-xml".to_string(), "geojson".to_string()],
        &[],
    )
    .unwrap()
}

fn kind(content: Option<&Content>) -> &'static str {
    match content {
        Some(Content::Registration(_)) => "registration",
        Some(Content::RegistrationAck(_)) => "registration_ack",
        Some(Content::StatusReport(_)) => "status_report",
        Some(Content::DetectionReport(_)) => "detection_report",
        Some(Content::Task(_)) => "task",
        Some(Content::TaskAck(_)) => "task_ack",
        Some(Content::Alert(_)) => "alert",
        Some(Content::AlertAck(_)) => "alert_ack",
        Some(Content::Error(_)) => "error",
        None => "empty",
    }
}

#[test]
fn every_accepted_reference_vector_converts_is_skipped_or_fails_by_name() {
    let dir = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../arms/sapient-receiver/tests/vectors/true");
    let t = translator();
    // (message kind, target) -> (converted, skipped, failed by engine error name)
    let mut seen: BTreeMap<(&str, &str), Tally> = BTreeMap::new();
    let mut vectors = 0;
    for entry in std::fs::read_dir(dir).unwrap() {
        let path = entry.unwrap().path();
        let message =
            sapient_receiver::json::decode(&std::fs::read_to_string(&path).unwrap()).unwrap();
        let bytes = message.encode_to_vec();
        vectors += 1;
        for target in ["cot-xml", "geojson"] {
            let slot = seen
                .entry((kind(message.content.as_ref()), target))
                .or_default();
            match t.translate(target, &bytes, &[]) {
                Ok(out) => {
                    assert!(!out.is_empty(), "{}", path.display());
                    slot.0 += 1;
                }
                Err(why) if why.outcome == Outcome::Skipped => slot.1 += 1,
                Err(why) => {
                    assert_ne!(why.name, "engine-panic", "{}: {why}", path.display());
                    *slot.2.entry(why.name).or_default() += 1;
                }
            }
        }
    }
    assert_eq!(vectors, 38);
    // Only a detection holds a position, and neither target has a field for anything
    // else of the other messages. Two reference detections give an x-y-z location in
    // UTM metres: the position guard skips them for both targets, since the SAPIENT pack
    // would write those metres as degrees. The third gives its position as range and
    // bearing, which the SAPIENT pack does not map. GeoJSON has no field for the rest of
    // it, so it is skipped there. CoT does: its free-text `state` is
    // `track.status_text`, which cot-xml writes as `detail/remarks` (goat-transformers
    // spec 0.18, #166), so it converts to an event that has no position.
    for ((message, target), (converted, skipped, failed)) in &seen {
        assert!(failed.is_empty(), "{message} to {target}: {failed:?}");
        let expected = match (*message, *target) {
            ("detection_report", "cot-xml") => (1, 2),
            ("detection_report", _) => (0, 3),
            _ => (0, *skipped),
        };
        assert_eq!((*converted, *skipped), expected, "{message} to {target}");
    }
    assert_eq!(seen.len(), 18, "{:?}", seen.keys());
}

/// The pinned SAPIENT pack reads `Location.x` and `Location.y` as longitude and latitude
/// whatever coordinate system the message declares. The gateway's position guard is on
/// by default: a detection whose location is in radians (2), UTM metres (5) or declares
/// none is skipped, and the same detection in latitude and longitude in degrees (1) is
/// converted with its numbers as degrees.
#[test]
fn a_detection_is_converted_only_in_degrees_and_skipped_in_radians_utm_or_unspecified() {
    let t = translator();
    let path = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../arms/sapient-receiver/tests/vectors/true/DetectionReport.FullMessage.json");
    let reference =
        sapient_receiver::json::decode(&std::fs::read_to_string(path).unwrap()).unwrap();
    let with_system = |system: Option<i32>| {
        let mut message = reference.clone();
        let Some(Content::DetectionReport(report)) = &mut message.content else {
            panic!("not a DetectionReport");
        };
        let Some(sapient_proto::v2::detection_report::LocationOneof::Location(location)) =
            &mut report.location_oneof
        else {
            panic!("no x-y-z location");
        };
        location.coordinate_system = system;
        message.encode_to_vec()
    };
    for (system, shown) in [(Some(2), "2"), (Some(5), "5"), (None, "absent")] {
        for target in ["cot-xml", "geojson"] {
            let why = t.translate(target, &with_system(system), &[]).unwrap_err();
            assert_eq!(why.outcome, Outcome::Skipped, "{shown} to {target}: {why}");
            assert_eq!(
                why.name, "coordinate-system-not-expressed",
                "{shown}: {why}"
            );
            assert!(
                why.detail
                    .starts_with(&format!("sapient.location_coordinate_system is {shown}:")),
                "{why}"
            );
        }
    }
    for target in ["cot-xml", "geojson"] {
        let out = t.translate(target, &with_system(Some(1)), &[]).unwrap();
        assert!(!out.is_empty(), "degrees to {target}");
    }
}
