// SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
// SPDX-License-Identifier: Apache-2.0
//! The whole gateway against a real Zenoh router in the test process (the arms' test
//! pen, `octo_arm_common::testing`), with the pack bundle the image ships and nothing
//! mocked: a captured sample goes into the pen as an arm would publish it, and the test
//! reads what the gateway publishes.
//!
//! The SAPIENT input is an unmodified BSI Flex 335 v2.0 reference vector from
//! `arms/sapient-receiver/tests/vectors`, encoded to protobuf with the arms' own
//! definitions: the bytes a SAPIENT arm captures.
#![allow(clippy::unwrap_used, clippy::expect_used, clippy::panic)]

use std::path::{Path, PathBuf};
use std::time::Duration;

use octo_arm_common::testing::{next_sample, TestPen, ARRIVES, QUIET};
use octo_translate::audit::payload_hash;
use octo_translate::gateway::{
    gateway, Declaration, DEFAULT_MAX_AGE, DEFAULT_MAX_BYTES, DEFAULT_QUEUE, REFUSED,
};
use octo_translate::route::DEFAULT_TEMPLATE;
use prost::Message;
use sapient_proto::v2::{
    detection_report::LocationOneof, sapient_message::Content, Location, SapientMessage,
};
use serde_json::Value;
use tokio::sync::oneshot;
use zenoh::bytes::Encoding;
use zenoh::sample::Sample;

type Sub = zenoh::pubsub::Subscriber<zenoh::handlers::FifoChannelHandler<Sample>>;

const SAPIENT_KEY: &str = "raw/radar1/sapient/192-0-2-50";
const COT_KEY: &str = "raw/tak/cot/192-0-2-60";
const COT: &str = r#"<event version="2.0" uid="J-01334" type="a-f-G-U-C" time="2021-02-27T20:00:00.000Z" start="2021-02-27T20:00:00.000Z" stale="2021-02-27T20:02:00.000Z" how="m-g"><point lat="30.0090027" lon="-85.9578182" hae="-42.6" ce="45.3" le="99.5"/><detail><contact callsign="GATOR"/></detail></event>"#;

/// The reference `DetectionReport`, as the protobuf bytes a SAPIENT arm captures.
fn reference_detection() -> (SapientMessage, Vec<u8>) {
    let path = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../arms/sapient-receiver/tests/vectors/true/DetectionReport.FullMessage.json");
    let mut message =
        sapient_receiver::json::decode(&std::fs::read_to_string(path).unwrap()).unwrap();
    // The reference vector declares UTM metres and holds values in the range of
    // degrees. The pinned SAPIENT pack reads x and y as longitude and latitude for any
    // coordinate system, so the gateway is declared with `only_when` on the system, and
    // this sample declares latitude and longitude.
    if let Some(Content::DetectionReport(report)) = &mut message.content {
        if let Some(LocationOneof::Location(location)) = &mut report.location_oneof {
            location.coordinate_system = Some(1);
        }
    }
    let bytes = message.encode_to_vec();
    (message, bytes)
}

fn scratch(name: &str) -> String {
    let dir =
        std::env::temp_dir().join(format!("octo-translate-pen-{}-{name}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    dir.to_string_lossy().into_owned()
}

fn audit_lines(dir: &str, id: &str) -> Vec<Value> {
    std::fs::read_to_string(PathBuf::from(dir).join(format!("{id}-translate.ndjson")))
        .unwrap_or_default()
        .lines()
        .map(|l| serde_json::from_str(l).expect("every audit line is JSON"))
        .collect()
}

fn declaration(pen: &TestPen, dir: &str, source: &str, from: &str, to: &[&str]) -> Declaration {
    Declaration {
        id: "xl1".into(),
        sources: vec![source.into()],
        from: from.into(),
        to: to.iter().map(ToString::to_string).collect(),
        namespace: "raw/xl1".into(),
        publish_key: DEFAULT_TEMPLATE.into(),
        context: vec![("how".into(), "m-g".into())],
        only_when: Vec::new(),
        stale_after: Some(Duration::from_secs(30)),
        stamp_time: true,
        packs_dir: Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("packs")
            .to_string_lossy()
            .into_owned(),
        max_distribution: "public".into(),
        pen: pen.client(),
        audit_dir: dir.into(),
        stats_secs: 1,
        queue: DEFAULT_QUEUE,
        max_bytes: DEFAULT_MAX_BYTES,
        // A loaded test runner may hold a sample longer than a pod would.
        max_age: DEFAULT_MAX_AGE * 20,
    }
}

fn start(d: Declaration) -> (oneshot::Sender<()>, tokio::task::JoinHandle<u8>) {
    let (stop, stopped) = oneshot::channel::<()>();
    let task = tokio::spawn(gateway(d, async move {
        let _ = stopped.await;
        Ok(())
    }));
    (stop, task)
}

/// Publish the captured sample until the gateway's subscription is up and a result
/// arrives, then return every result for that one input (one per target).
async fn capture_until_translated(
    pen: &TestPen,
    results: &Sub,
    key: &str,
    payload: &[u8],
    targets: usize,
) -> Vec<Sample> {
    let deadline = tokio::time::Instant::now() + ARRIVES * 3;
    let first = loop {
        pen.session
            .put(key, payload.to_vec())
            .encoding(Encoding::APPLICATION_OCTET_STREAM)
            .await
            .unwrap();
        if let Some(s) = next_sample(results, Duration::from_millis(500)).await {
            break s;
        }
        assert!(tokio::time::Instant::now() < deadline, "never translated");
    };
    let mut out = vec![first];
    while out.len() < targets {
        out.push(
            next_sample(results, ARRIVES)
                .await
                .expect("one result per target"),
        );
    }
    out
}

fn by_key<'a>(samples: &'a [Sample], key: &str) -> &'a Sample {
    samples
        .iter()
        .find(|s| s.key_expr().as_str() == key)
        .unwrap_or_else(|| panic!("no result on {key}"))
}

fn attachment(sample: &Sample) -> Value {
    serde_json::from_slice(
        &sample
            .attachment()
            .expect("provenance attachment")
            .to_bytes(),
    )
    .unwrap()
}

/// The numeric value of one XML attribute, by name with its leading space.
fn number(xml: &str, attribute: &str) -> f64 {
    let start = xml
        .find(attribute)
        .unwrap_or_else(|| panic!("no{attribute} in {xml}"))
        + attribute.len();
    let end = start + xml[start..].find('"').unwrap();
    xml[start..end].parse().unwrap()
}

/// The x, y, z of a detection's location.
fn xyz(message: &SapientMessage) -> (f64, f64, f64) {
    let Some(Content::DetectionReport(report)) = &message.content else {
        panic!("not a DetectionReport: {message:?}");
    };
    let Some(LocationOneof::Location(Location { x, y, z, .. })) = &report.location_oneof else {
        panic!("no x-y-z location: {report:?}");
    };
    (x.unwrap(), y.unwrap(), z.unwrap())
}

fn text(sample: &Sample) -> String {
    String::from_utf8(sample.payload().to_bytes().to_vec()).unwrap()
}

#[tokio::test(flavor = "multi_thread")]
async fn a_captured_sapient_detection_is_published_as_cot_xml_and_geojson_with_provenance() {
    let pen = TestPen::start().await;
    let results = pen.subscribe("raw/xl1/**").await;
    let captured = pen.subscribe("raw/radar1/**").await;
    let dir = scratch("sapient");
    let declared = declaration(
        &pen,
        &dir,
        "raw/radar1/sapient/*",
        "sapient",
        &["cot-xml", "geojson"],
    );
    let (stop, task) = start(declared);

    let (message, bytes) = reference_detection();
    let (x, y, z) = xyz(&message);
    let out = capture_until_translated(&pen, &results, SAPIENT_KEY, &bytes, 2).await;

    // CoT XML: the position the pinned SAPIENT pack reads (Location.x as longitude,
    // Location.y as latitude, Location.z as height), and the declared context.
    let cot = by_key(&out, &format!("raw/xl1/cot-xml/{SAPIENT_KEY}"));
    assert_eq!(*cot.encoding(), Encoding::APPLICATION_XML);
    let xml = text(cot);
    assert!(xml.starts_with(r#"<event version="2.0""#), "{xml}");
    assert!(xml.contains("<point "), "{xml}");
    for (attribute, expected) in [(r#" lon=""#, x), (r#" lat=""#, y), (r#" hae=""#, z)] {
        assert!(
            (number(&xml, attribute) - expected).abs() < 1e-9,
            "{attribute} in {xml}"
        );
    }
    assert!(xml.contains(r#"how="m-g""#), "{xml}");
    // No uid: the SAPIENT pack leaves the object id raw, and the gateway maps nothing.
    assert!(!xml.contains(" uid="), "{xml}");
    // The values only the host knows: when the sample was received, and stale 30 s on.
    assert!(
        xml.contains(r#" time="20"#) && xml.contains(r#" stale="20"#),
        "{xml}"
    );

    // GeoJSON: the same position, as a Feature.
    let geo = by_key(&out, &format!("raw/xl1/geojson/{SAPIENT_KEY}"));
    assert_eq!(geo.encoding().to_string(), "application/geo+json");
    let feature: Value = serde_json::from_str(&text(geo)).unwrap();
    assert_eq!(feature["type"], "Feature");
    let coordinates = feature["geometry"]["coordinates"].as_array().unwrap();
    for (got, expected) in coordinates.iter().zip([x, y, z]) {
        assert!(
            (got.as_f64().unwrap() - expected).abs() < 1e-9,
            "{coordinates:?}"
        );
    }
    assert_eq!(coordinates.len(), 3);

    // Provenance on each result: the key, hash and format of the captured sample.
    for (sample, to) in [(cot, "cot-xml"), (geo, "geojson")] {
        let p = attachment(sample);
        assert_eq!(p["source_key"], SAPIENT_KEY);
        assert_eq!(p["source_hash"], payload_hash(&bytes));
        assert_eq!(p["from"], "sapient");
        assert_eq!(p["to"], to);
        assert_eq!(p["gateway"], "xl1");
        assert_eq!(p["packs_commit"].as_str().unwrap().len(), 40);
    }

    // The captured sample is still exactly what the arm published.
    let raw = next_sample(&captured, ARRIVES).await.expect("the capture");
    assert_eq!(raw.key_expr().as_str(), SAPIENT_KEY);
    assert_eq!(raw.payload().to_bytes().as_ref(), bytes.as_slice());
    assert_eq!(*raw.encoding(), Encoding::APPLICATION_OCTET_STREAM);
    assert!(raw.attachment().is_none());

    tokio::time::sleep(Duration::from_millis(1_100)).await;
    stop.send(()).unwrap();
    assert_eq!(task.await.unwrap(), 0);

    let lines = audit_lines(&dir, "xl1");
    assert_eq!(lines[0]["kind"], "start");
    assert_eq!(lines[0]["from"], "sapient");
    assert_eq!(lines[0]["max_distribution"], "public");
    let translated = lines
        .iter()
        .find(|l| l["kind"] == "translated" && l["to"] == "cot-xml")
        .expect("a translated line");
    assert_eq!(translated["source_key"], SAPIENT_KEY);
    assert_eq!(translated["source_hash"], payload_hash(&bytes));
    assert_eq!(translated["key"], format!("raw/xl1/cot-xml/{SAPIENT_KEY}"));
    assert_eq!(
        translated["payload_hash"],
        payload_hash(&cot.payload().to_bytes())
    );
    let summary = lines
        .iter()
        .find(|l| l["kind"] == "summary")
        .expect("a summary");
    assert!(summary["translated"].as_u64().unwrap() >= 2);
    assert_eq!(summary["failed"], 0);
}

#[tokio::test(flavor = "multi_thread")]
async fn a_captured_cot_event_is_published_as_sapient_protobuf_and_geojson() {
    let pen = TestPen::start().await;
    let results = pen.subscribe("raw/xl1/**").await;
    let dir = scratch("cot");
    let (stop, task) = start(declaration(
        &pen,
        &dir,
        "raw/tak/cot/*",
        "cot-xml",
        &["sapient", "geojson"],
    ));

    let out = capture_until_translated(&pen, &results, COT_KEY, COT.as_bytes(), 2).await;

    // SAPIENT: protobuf bytes that the arms' own definitions decode as a DetectionReport
    // at the event's position.
    let sapient = by_key(&out, &format!("raw/xl1/sapient/{COT_KEY}"));
    assert_eq!(
        sapient.encoding().to_string(),
        "application/protobuf;sapient"
    );
    let message = SapientMessage::decode(sapient.payload().to_bytes().as_ref()).unwrap();
    let (x, y, z) = xyz(&message);
    assert!((x - -85.957_818_2).abs() < 1e-9, "{x}");
    assert!((y - 30.009_002_7).abs() < 1e-9, "{y}");
    assert!((z - -42.6).abs() < 1e-9, "{z}");

    let geo = by_key(&out, &format!("raw/xl1/geojson/{COT_KEY}"));
    let feature: Value = serde_json::from_str(&text(geo)).unwrap();
    assert_eq!(feature["id"], "J-01334");
    assert_eq!(feature["properties"]["callsign"], "GATOR");
    // The event carries its own time; the receive time did not replace it.
    assert_eq!(feature["properties"]["time_ms"], 1_614_456_000_000_i64);
    assert_eq!(attachment(geo)["source_hash"], payload_hash(COT.as_bytes()));

    stop.send(()).unwrap();
    assert_eq!(task.await.unwrap(), 0);
}

#[tokio::test(flavor = "multi_thread")]
async fn a_sample_that_does_not_convert_is_counted_and_recorded_and_never_published() {
    let pen = TestPen::start().await;
    let results = pen.subscribe("raw/xl1/**").await;
    let dir = scratch("failure");
    let mut d = declaration(&pen, &dir, "raw/tak/cot/*", "cot-xml", &["geojson"]);
    d.max_bytes = 4096;
    let (stop, task) = start(d);

    // A good sample first: proves the subscription is up before the bad ones are sent.
    capture_until_translated(&pen, &results, COT_KEY, COT.as_bytes(), 1).await;
    while next_sample(&results, Duration::from_millis(300))
        .await
        .is_some()
    {}

    // Cut inside the root tag; cut after an attribute's `=` (an engine before
    // goat-transformers#12 panicked on it; it now reports malformed-wire); bytes that
    // are not text; and a sample over max_bytes.
    let truncated = &COT.as_bytes()[..6];
    let cut_after_equals = &COT.as_bytes()[..40];
    assert!(cut_after_equals.ends_with(b"type="));
    let oversize = vec![b' '; 5000];
    for bad in [
        truncated,
        cut_after_equals,
        &[0xff, 0xfe, 0xfd][..],
        oversize.as_slice(),
    ] {
        pen.session.put(COT_KEY, bad.to_vec()).await.unwrap();
    }
    // Nothing is published for any of them: no partial result, no source bytes.
    assert!(next_sample(&results, QUIET).await.is_none());

    // And the gateway is still converting.
    let again = capture_until_translated(&pen, &results, COT_KEY, COT.as_bytes(), 1).await;
    assert_eq!(
        again[0].key_expr().as_str(),
        format!("raw/xl1/geojson/{COT_KEY}")
    );

    tokio::time::sleep(Duration::from_millis(1_100)).await;
    stop.send(()).unwrap();
    assert_eq!(task.await.unwrap(), 0);

    let lines = audit_lines(&dir, "xl1");
    let failed: Vec<&Value> = lines.iter().filter(|l| l["kind"] == "failed").collect();
    assert_eq!(failed.len(), 3, "{failed:?}");
    assert_eq!(failed[0]["source_key"], COT_KEY);
    assert_eq!(failed[0]["source_hash"], payload_hash(truncated));
    assert_eq!(failed[0]["to"], "geojson");
    assert!(
        failed[0]["detail"]
            .as_str()
            .unwrap()
            .starts_with("decode: "),
        "{failed:?}"
    );
    assert_eq!(
        failed[0]["detail"],
        "decode: malformed-wire: xml: unexpected end in tag <event>"
    );
    assert_eq!(failed[1]["error"], "malformed-wire");
    assert_eq!(failed[1]["source_hash"], payload_hash(cut_after_equals));
    assert_eq!(failed[2]["error"], "not-text");
    let stop_line = lines
        .iter()
        .find(|l| l["kind"] == "stop")
        .expect("a stop line");
    assert_eq!(stop_line["failed"], 3);
    assert_eq!(stop_line["dropped_oversize"], 1);
    assert_eq!(stop_line["errors"]["not-text"], 1);
    assert!(stop_line["translated"].as_u64().unwrap() >= 2);
}

#[tokio::test(flavor = "multi_thread")]
async fn a_declaration_it_cannot_serve_exits_2_before_opening_anything() {
    let pen = TestPen::start().await;
    let results = pen.subscribe("**").await;
    for (name, change) in [
        (
            "loop",
            Box::new(|d: &mut Declaration| d.sources = vec!["raw/**".into()])
                as Box<dyn Fn(&mut Declaration)>,
        ),
        (
            "format",
            Box::new(|d: &mut Declaration| d.to = vec!["link16".into()]),
        ),
        (
            "context",
            Box::new(|d: &mut Declaration| {
                d.context = vec![("nosuchkey".into(), "1".into())];
            }),
        ),
        (
            "distribution",
            Box::new(|d: &mut Declaration| d.max_distribution = "any".into()),
        ),
        (
            "bundle",
            Box::new(|d: &mut Declaration| d.packs_dir = "/nonexistent".into()),
        ),
    ] {
        let dir = scratch(name);
        let mut d = declaration(&pen, &dir, "raw/tak/cot/*", "cot-xml", &["geojson"]);
        change(&mut d);
        assert_eq!(gateway(d, async { Ok(()) }).await, REFUSED, "{name}");
        let lines = audit_lines(&dir, "xl1");
        assert_eq!(lines.len(), 1, "{name}");
        assert_eq!(lines[0]["kind"], "refused", "{name}");
    }
    assert!(next_sample(&results, QUIET).await.is_none());
}

#[tokio::test(flavor = "multi_thread")]
async fn a_pen_that_will_not_open_exits_1_for_the_supervisor() {
    let pen = TestPen::start().await;
    let dir = scratch("nopen");
    let mut d = declaration(&pen, &dir, "raw/tak/cot/*", "cot-xml", &["geojson"]);
    d.pen.endpoint = format!(
        "tcp/127.0.0.1:{}",
        octo_arm_common::testing::free_tcp_port()
    );
    assert_eq!(gateway(d, async { Ok(()) }).await, 1);
}
