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
