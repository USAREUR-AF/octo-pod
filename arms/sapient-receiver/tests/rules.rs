// SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
// SPDX-License-Identifier: Apache-2.0
//! Focused tests reach semantic rules even when an upstream false vector has a
//! separate JSON error. These synthetic mutations do not alter the oracle corpus.
use sapient_proto::v2::{sapient_message::Content, RegistrationAck, SapientMessage};
use sapient_receiver::{json, validate};
use serde_json::Value;

fn seed(name: &str) -> Value {
    let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("tests/vectors/true")
        .join(name);
    serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap()
}
fn reject(value: Value, field: &str) {
    let msg =
        json::decode(&value.to_string()).expect("must reach semantic validation, not fail JSON");
    let errors = validate::violations(&msg);
    assert!(
        errors.iter().any(|e| e.contains(field)),
        "missing {field}: {errors:?}"
    );
}
#[test]
fn detection_rules_are_not_masked_by_obsolete_json_fields() {
    for (field, bad, expected) in [
        ("reportId", Value::from("bad"), "report_id"),
        ("objectId", Value::from("bad"), "object_id"),
        (
            "detectionConfidence",
            Value::from(1.1),
            "detection_confidence",
        ),
    ] {
        let mut v = seed("DetectionReport.FullMessage.json");
        v["detectionReport"][field] = bad;
        reject(v, expected);
    }
    let mut v = seed("DetectionReport.FullMessage.json");
    v["detectionReport"]
        .as_object_mut()
        .unwrap()
        .remove("location");
    reject(v, "location");
}
#[test]
fn registration_gate_checks_more_than_region() {
    for (field, bad, expected) in [
        ("icdVersion", Value::from("wrong"), "icd_version"),
        ("capabilities", serde_json::json!([]), "capabilities"),
        ("configData", serde_json::json!([]), "config_data"),
        ("modeDefinition", serde_json::json!([]), "mode_definition"),
    ] {
        let mut v = seed("Registration.FollowObject.json");
        v["registration"][field] = bad;
        reject(v, expected);
    }
}
#[test]
fn protobuf_presence_is_not_truthiness() {
    let mut msg = SapientMessage {
        timestamp: Some(prost_types::Timestamp {
            seconds: 0,
            nanos: 0,
        }),
        node_id: Some("00000000-0000-4000-8000-00000000aaaa".into()),
        content: Some(Content::RegistrationAck(RegistrationAck {
            acceptance: Some(false),
            ack_response_reason: vec![],
        })),
        ..Default::default()
    };
    // The pinned reference only requires explicit acceptance. It does not enforce
    // destination_id or a reason on rejection; the receiver still emits both.
    assert!(validate::violations(&msg).is_empty());
    if let Some(Content::RegistrationAck(a)) = &mut msg.content {
        a.acceptance = None;
    }
    assert!(validate::violations(&msg)
        .iter()
        .any(|e| e.contains("acceptance")));
}
#[test]
fn duplicate_oneofs_unknown_fields_and_trailing_input_fail_decode() {
    for source in [
        r#"{"bogus":1}"#,
        r#"{"registration":{},"statusReport":{}}"#,
        "{} {}",
    ] {
        assert!(json::decode(source).is_err(), "{source}");
    }
}

fn location_type(v: &mut Value) -> &mut Value {
    &mut v["registration"]["modeDefinition"][0]["detectionDefinition"][0]["locationType"]
}

fn command(v: &mut Value) -> &mut Value {
    &mut v["task"]["command"]
}

fn accept(value: Value) {
    let msg = json::decode(&value.to_string()).expect("must decode");
    let errors = validate::violations(&msg);
    assert!(errors.is_empty(), "expected no violations: {errors:?}");
}

#[test]
fn a_dependent_node_must_be_a_uuid_when_it_is_given() {
    let mut v = seed("Registration.FollowObject.json");
    v["registration"]["dependentNodes"] =
        serde_json::json!(["6f582b41-6c88-481c-bb0f-6021a1739f57", "sensor-2"]);
    reject(v, "dependent_nodes");
    // A blank entry names no node, so it is not an identifier to check.
    let mut v = seed("Registration.FollowObject.json");
    v["registration"]["dependentNodes"] = serde_json::json!(["  "]);
    accept(v);
}

#[test]
fn unspecified_region_types_and_filter_operators_are_refused() {
    let mut v = seed("Registration.FollowObject.json");
    v["registration"]["modeDefinition"][0]["task"]["regionDefinition"]["regionType"] =
        serde_json::json!(["REGION_TYPE_AREA_OF_INTEREST", "REGION_TYPE_UNSPECIFIED"]);
    reject(v, "region_type");

    let mut v = seed("Registration.FollowObject.json");
    v["registration"]["modeDefinition"][0]["task"]["regionDefinition"]["classFilterDefinition"]
        [0]["filterParameter"][0]["operators"] = serde_json::json!(["OPERATOR_UNSPECIFIED"]);
    reject(v, "operators");
}

#[test]
fn a_detection_location_is_a_complete_position_or_a_complete_range_and_bearing() {
    // Range and bearing alone is a complete answer.
    let mut v = seed("Registration.FollowObject.json");
    *location_type(&mut v) = serde_json::json!({
        "rangeBearingUnits": "RANGE_BEARING_COORDINATE_SYSTEM_DEGREES_M",
        "rangeBearingDatum": "RANGE_BEARING_DATUM_MAGNETIC"
    });
    accept(v);
    // (Both at once cannot be sent: units and datums are each a protobuf oneof.)
    // A position with no datum is not a position.
    let mut v = seed("Registration.FollowObject.json");
    location_type(&mut v)
        .as_object_mut()
        .unwrap()
        .remove("locationDatum");
    reject(v, "location_units");
    // A range/bearing with an unspecified unit is not a range/bearing.
    let mut v = seed("Registration.FollowObject.json");
    *location_type(&mut v) = serde_json::json!({
        "rangeBearingUnits": "RANGE_BEARING_COORDINATE_SYSTEM_UNSPECIFIED",
        "rangeBearingDatum": "RANGE_BEARING_DATUM_MAGNETIC"
    });
    reject(v, "location_units");
    // UTM needs its zone; latitude/longitude does not.
    let mut v = seed("Registration.FollowObject.json");
    location_type(&mut v)
        .as_object_mut()
        .unwrap()
        .remove("zone");
    reject(v, "zone");
    let mut v = seed("Registration.FollowObject.json");
    location_type(&mut v)["locationUnits"] =
        Value::from("LOCATION_COORDINATE_SYSTEM_LAT_LNG_DEG_M");
    location_type(&mut v)
        .as_object_mut()
        .unwrap()
        .remove("zone");
    accept(v);
}

#[test]
fn a_task_command_must_be_present_named_and_specified() {
    let mut v = seed("Task.FullMessage.Request.Start.json");
    *command(&mut v) = serde_json::json!({ "commandParameter": "" });
    reject(v, "command");

    let mut v = seed("Task.FullMessage.Request.Start.json");
    command(&mut v)["request"] = Value::from("");
    reject(v, "request");

    let mut v = seed("Task.FullMessage.ModeChange.json");
    command(&mut v)["modeChange"] = Value::from("");
    reject(v, "mode_change");

    let mut v = seed("Task.FullMessage.DetectionThreshold.Low.json");
    command(&mut v)["detectionThreshold"] = Value::from("DISCRETE_THRESHOLD_UNSPECIFIED");
    reject(v, "detection_threshold");

    for ok in [
        "Task.FullMessage.DetectionThreshold.Low.json",
        "Task.FullMessage.ModeChange.json",
        "Task.FullMessage.Request.Start.json",
        "Task.FullMessage.Patrol.json",
    ] {
        accept(seed(ok));
    }
}

#[test]
fn a_behaviour_outside_the_standard_list_is_refused() {
    let mut v = seed("DetectionReport.ThreeClassProbabilities.FullMessage.json");
    v["detectionReport"]["behaviour"] =
        serde_json::json!([{ "type": "dancing", "confidence": 0.5 }]);
    reject(v, "type");
    let mut v = seed("DetectionReport.ThreeClassProbabilities.FullMessage.json");
    v["detectionReport"]["behaviour"] =
        serde_json::json!([{ "type": "running", "confidence": 0.5 }]);
    accept(v);
}
