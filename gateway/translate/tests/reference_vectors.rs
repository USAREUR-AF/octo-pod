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

fn rule(key: &str, value: &str) -> Vec<(String, String)> {
    vec![(key.to_string(), value.to_string())]
}

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
    // else. One reference detection gives its position as range and bearing, which the
    // SAPIENT pack does not map, so it is skipped too.
    for ((message, target), (converted, skipped, failed)) in &seen {
        assert!(failed.is_empty(), "{message} to {target}: {failed:?}");
        let expected = if *message == "detection_report" {
            (2, 1)
        } else {
            (0, *skipped)
        };
        assert_eq!((*converted, *skipped), expected, "{message} to {target}");
    }
    assert_eq!(seen.len(), 18, "{:?}", seen.keys());
}

/// The pinned SAPIENT pack reads `Location.x` and `Location.y` as longitude and latitude
/// whatever coordinate system the message declares. Every reference detection with an
/// x-y-z location declares UTM metres. A gateway declared with `only_when` on the
/// coordinate system skips them, and converts the same detection once it declares
/// latitude and longitude; `carry` then gives the result the detection's object id.
#[test]
fn a_gateway_declared_for_latitude_and_longitude_skips_the_utm_reference_detections() {
    let t = translator()
        .with_rules(
            &rule("sapient.location_coordinate_system", "1"),
            &rule("uid", "sapient.detection_report_object_id"),
        )
        .unwrap();
    let dir = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../arms/sapient-receiver/tests/vectors/true");
    for name in [
        "DetectionReport.FullMessage.json",
        "DetectionReport.ThreeClassProbabilities.FullMessage.json",
    ] {
        let mut message =
            sapient_receiver::json::decode(&std::fs::read_to_string(dir.join(name)).unwrap())
                .unwrap();
        let why = t
            .translate("cot-xml", &message.encode_to_vec(), &[])
            .unwrap_err();
        assert_eq!(why.outcome, Outcome::Skipped, "{name}: {why}");
        assert_eq!(why.name, "condition-not-met", "{name}: {why}");
        assert!(why.detail.ends_with("the message has 5"), "{name}: {why}");

        let Some(Content::DetectionReport(report)) = &mut message.content else {
            panic!("{name} is not a DetectionReport");
        };
        let Some(sapient_proto::v2::detection_report::LocationOneof::Location(location)) =
            &mut report.location_oneof
        else {
            panic!("{name} has no x-y-z location");
        };
        location.coordinate_system = Some(1);
        let object_id = report.object_id.clone().unwrap();
        let xml = String::from_utf8(
            t.translate("cot-xml", &message.encode_to_vec(), &[])
                .unwrap(),
        )
        .unwrap();
        assert!(
            xml.contains(&format!(r#" uid="{object_id}""#)),
            "{name}: {xml}"
        );
    }
}
