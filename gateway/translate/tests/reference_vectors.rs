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
    // Only a detection holds a value the targets have a field for, and neither target has
    // one for anything of the other messages. Two reference detections give an x-y-z
    // location in UTM metres and the third a range and bearing: the SAPIENT pack maps
    // neither to a position, since it reads x and y as longitude and latitude only in
    // degrees (goat-transformers#312). GeoJSON then has no field for anything they
    // carry (the uid the pack derives from the object id is not counted: the gateway
    // counts what the message itself carries, before derivations), so they are skipped
    // there. CoT has one, the free-text `state` (`detail/remarks`), so each converts to
    // an event with no position and the derived uid. Nothing is written as degrees.
    for ((message, target), (converted, skipped, failed)) in &seen {
        assert!(failed.is_empty(), "{message} to {target}: {failed:?}");
        let expected = match (*message, *target) {
            ("detection_report", "cot-xml") => (3, 0),
            ("detection_report", _) => (0, 3),
            _ => (0, *skipped),
        };
        assert_eq!((*converted, *skipped), expected, "{message} to {target}");
    }
    assert_eq!(seen.len(), 18, "{:?}", seen.keys());
}

/// The pinned SAPIENT pack reads `Location.x`, `y` and `z` as longitude, latitude and
/// height only when `Location.coordinate_system` is 1, degrees (goat-transformers#312).
/// The same reference detection in radians (2), UTM metres (5) or with no system has no
/// position: `GeoJSON` skips it (nothing it carries has a `GeoJSON` field) and `CoT`
/// writes an event with no lat or lon. In degrees, both write its numbers as the position.
#[test]
fn a_detection_has_a_position_only_in_degrees() {
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
        let why = t
            .translate("geojson", &with_system(system), &[])
            .unwrap_err();
        assert_eq!(why.outcome, Outcome::Skipped, "{shown}: {why}");
        assert_eq!(why.name, "no-shared-value", "{shown}: {why}");
        let xml =
            String::from_utf8(t.translate("cot-xml", &with_system(system), &[]).unwrap()).unwrap();
        // cot-xml writes `point` with its "not known" values; no lat or lon is written.
        assert!(
            !xml.contains(" lat=") && !xml.contains(" lon="),
            "{shown}: {xml}"
        );
    }
    let geo: serde_json::Value =
        serde_json::from_slice(&t.translate("geojson", &with_system(Some(1)), &[]).unwrap())
            .unwrap();
    assert!(geo["geometry"]["coordinates"].is_array(), "degrees: {geo}");
    let xml =
        String::from_utf8(t.translate("cot-xml", &with_system(Some(1)), &[]).unwrap()).unwrap();
    assert!(
        xml.contains(" lat=") && xml.contains(" lon="),
        "degrees: {xml}"
    );
}
