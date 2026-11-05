//! The only SAPIENT this arm speaks, and the only SAPIENT it reads.
//!
//! Everything here serves the session and nothing else: the messages the arm must send to
//! be a participant, and the few fields it must read to know where it stands. A
//! `DetectionReport` is published raw and never looked inside — turning one into another
//! format is a translator's job (`docs/engineering-principles.md`, "Capture-first").
//!
//! The registration is the interesting part. The standard marks a set of fields mandatory
//! and nests some of them three deep, so a registration that omits them is rejected by a
//! strict peer with no useful reason. This builds the complete mandatory set, describing
//! exactly what this arm is: a relay with one permanent mode that accepts no tasking. A
//! peer that wants more says so in `ack_response_reason`, which the session publishes
//! verbatim rather than paraphrasing.

use std::time::{Duration, SystemTime, UNIX_EPOCH};

use anyhow::{bail, Context, Result};
use prost::Message;

use crate::framing::ByteOrder;
use crate::identity::ulid;
use crate::proto::v2::{
    registration, sapient_message, status_report, task_ack, LocationCoordinateSystem,
    LocationDatum, Registration, RegistrationAck, SapientMessage, StatusReport, TaskAck,
};
use crate::session::{PeerConfig, Role};

/// What this arm reports itself as. Not configurable: it describes the software, and an
/// operator editing it would be describing something that is not running.
const MANUFACTURER: &str = "octo-pod";
const MODEL: &str = "sapient arm";
/// The one mode this arm has. It relays; it does not scan, steer or point.
const MODE_NAME: &str = "relay";

/// Parse a node type from the standard's enum, with or without its prefix.
pub fn node_type(name: &str) -> Result<i32> {
    let upper = name.trim().to_ascii_uppercase();
    let qualified = if upper.starts_with("NODE_TYPE_") {
        upper
    } else {
        format!("NODE_TYPE_{upper}")
    };
    registration::NodeType::from_str_name(&qualified)
        .map(|t| t as i32)
        .with_context(|| {
            format!(
                "unknown node type `{name}` — it must be one of the standard's NodeType \
                 values, for example OTHER, RADAR, CAMERA, LIDAR, ACOUSTIC, PASSIVE_RF"
            )
        })
}

/// A declared capability: category, type, and an optional value and units. The standard
/// makes the first two mandatory on every capability and leaves the other two to the node.
pub type Capability = (String, String, Option<String>, Option<String>);

/// Parse `category:type[:value[:units]]`, comma-separated.
pub fn capabilities(spec: &str) -> Result<Vec<Capability>> {
    let mut out = Vec::new();
    for part in spec.split(',').map(str::trim).filter(|s| !s.is_empty()) {
        let mut fields = part.split(':').map(str::trim);
        let (Some(category), Some(kind)) = (fields.next(), fields.next()) else {
            bail!("capability `{part}` is not `category:type[:value[:units]]`");
        };
        if category.is_empty() || kind.is_empty() {
            bail!("capability `{part}` has an empty category or type, both of which the standard requires");
        }
        out.push((
            category.to_string(),
            kind.to_string(),
            fields.next().filter(|s| !s.is_empty()).map(str::to_string),
            fields.next().filter(|s| !s.is_empty()).map(str::to_string),
        ));
    }
    Ok(out)
}

/// The envelope every SAPIENT message on the wire is wrapped in.
fn envelope(node_id: &str, content: sapient_message::Content) -> SapientMessage {
    SapientMessage {
        timestamp: Some(now()),
        node_id: Some(node_id.to_string()),
        destination_id: None,
        content: Some(content),
        additional_information: None,
    }
}

fn now() -> prost_types::Timestamp {
    let d = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default();
    prost_types::Timestamp {
        seconds: d.as_secs() as i64,
        nanos: d.subsec_nanos() as i32,
    }
}

fn seconds(d: Duration) -> registration::Duration {
    registration::Duration {
        units: Some(registration::TimeUnits::Seconds as i32),
        value: Some(d.as_secs_f32()),
    }
}

/// This arm's registration: what it is, what it can report, and how often it will speak.
pub fn registration(cfg: &PeerConfig) -> SapientMessage {
    let caps = cfg
        .capabilities
        .iter()
        .map(|(category, kind, value, units)| registration::Capability {
            category: Some(category.clone()),
            r#type: Some(kind.clone()),
            value: value.clone(),
            units: units.clone(),
        })
        .collect::<Vec<_>>();

    let reg = Registration {
        node_definition: vec![registration::NodeDefinition {
            node_type: Some(cfg.node_type),
            node_sub_type: vec![],
        }],
        icd_version: Some(cfg.icd_version.clone()),
        name: Some(format!("{MANUFACTURER} {MODEL}")),
        short_name: Some(MANUFACTURER.to_string()),
        // A capability list is mandatory, and an empty one is a node claiming to do
        // nothing. When the operator declared none, say the one true thing about this
        // arm rather than send an empty list a strict peer will reject.
        capabilities: if caps.is_empty() {
            vec![registration::Capability {
                category: Some("Relay".to_string()),
                r#type: Some("Captured feed".to_string()),
                value: None,
                units: None,
            }]
        } else {
            caps
        },
        status_definition: Some(registration::StatusDefinition {
            status_interval: Some(seconds(cfg.status_interval)),
            ..Default::default()
        }),
        mode_definition: vec![registration::ModeDefinition {
            mode_name: Some(MODE_NAME.to_string()),
            mode_type: Some(registration::ModeType::Permanent as i32),
            mode_description: Some("Relays a captured feed; accepts no tasking".to_string()),
            // There is no sensor here to warm up or slew. The pinned reference validator
            // rejects zero settle time, so declare effectively immediate readiness.
            settle_time: Some(seconds(Duration::from_millis(1))),
            task: Some(registration::TaskDefinition {
                concurrent_tasks: Some(0),
                region_definition: Some(registration::RegionDefinition {
                    region_type: vec![registration::RegionType::AreaOfInterest as i32],
                    // region_area is mandatory too (a strict controller refuses the
                    // registration without it). The arm relays a feed and owns no
                    // geometry, so it declares the coordinate language of its reports, as
                    // the proven opt8 gateway did: latitude and longitude in degrees, WGS84.
                    region_area: vec![registration::LocationType {
                        coordinates_oneof: Some(
                            registration::location_type::CoordinatesOneof::LocationUnits(
                                LocationCoordinateSystem::LatLngDegM as i32,
                            ),
                        ),
                        datum_oneof: Some(registration::location_type::DatumOneof::LocationDatum(
                            LocationDatum::Wgs84E as i32,
                        )),
                        zone: None,
                    }],
                    ..Default::default()
                }),
                command: vec![],
            }),
            ..Default::default()
        }],
        config_data: vec![registration::ConfigurationData {
            manufacturer: MANUFACTURER.to_string(),
            model: MODEL.to_string(),
            software_version: Some(env!("CARGO_PKG_VERSION").to_string()),
            ..Default::default()
        }],
        ..Default::default()
    };
    envelope(&cfg.node_id, sapient_message::Content::Registration(reg))
}

/// The heartbeat. Its absence is what deregisters this node, so it says the minimum the
/// standard requires and nothing that could fail to be true.
pub fn status_report(node_id: &str) -> SapientMessage {
    let report = StatusReport {
        report_id: Some(ulid()),
        system: Some(status_report::System::Ok as i32),
        info: Some(status_report::Info::Unchanged as i32),
        mode: Some(MODE_NAME.to_string()),
        ..Default::default()
    };
    envelope(node_id, sapient_message::Content::StatusReport(report))
}

/// Acknowledge a peer's registration. Only the DMM role sends this.
pub fn registration_ack(
    node_id: &str,
    destination_id: Option<String>,
    accepted: bool,
) -> SapientMessage {
    let mut msg = envelope(
        node_id,
        sapient_message::Content::RegistrationAck(RegistrationAck {
            acceptance: Some(accepted),
            ack_response_reason: vec![],
        }),
    );
    msg.destination_id = destination_id;
    msg
}

/// Reject a task, with the reason. The standard promises the peer an acknowledgement for
/// every task, so silence is not an option even when the answer is no.
pub fn task_ack(node_id: &str, task_id: &str, reason: &str) -> SapientMessage {
    envelope(
        node_id,
        sapient_message::Content::TaskAck(TaskAck {
            task_id: Some(task_id.to_string()),
            task_status: Some(task_ack::TaskStatus::Rejected as i32),
            reason: vec![reason.to_string()],
            associated_file: None,
        }),
    )
}

/// Frame a message: the 4-byte length prefix, then the body.
pub fn encode(msg: &SapientMessage, order: ByteOrder) -> Vec<u8> {
    let mut out = Vec::with_capacity(msg.encoded_len() + 4);
    out.extend_from_slice(&[0, 0, 0, 0]);
    msg.encode(&mut out)
        .expect("encoding into a Vec has no failure mode");
    let len = (out.len() - 4) as u32;
    let hdr = match order {
        ByteOrder::Le => len.to_le_bytes(),
        ByteOrder::Be => len.to_be_bytes(),
    };
    out[..4].copy_from_slice(&hdr);
    out
}

/// Decode a frame body, or `None` if it is not a SapientMessage.
pub fn decode(bytes: &[u8]) -> Option<SapientMessage> {
    SapientMessage::decode(bytes).ok()
}

/// Does this look like a SapientMessage? Used to break a tie between two plausible
/// readings of a length prefix, so it has to reject noise rather than merely parse it:
/// protobuf accepts a great deal, but an envelope with neither a header nor a content is
/// not a message anyone sent.
pub fn decodes(bytes: &[u8]) -> bool {
    match SapientMessage::decode(bytes) {
        Ok(m) => m.content.is_some() || m.node_id.is_some() || m.timestamp.is_some(),
        Err(_) => false,
    }
}

pub fn is_registration(msg: &SapientMessage) -> bool {
    matches!(msg.content, Some(sapient_message::Content::Registration(_)))
}

/// The acceptance and, when refused, the peer's own words for why.
pub fn registration_ack_of(msg: &SapientMessage) -> Option<(bool, Vec<String>)> {
    match &msg.content {
        Some(sapient_message::Content::RegistrationAck(ack)) => Some((
            ack.acceptance.unwrap_or(false),
            ack.ack_response_reason.clone(),
        )),
        _ => None,
    }
}

pub fn task_id_of(msg: &SapientMessage) -> Option<String> {
    match &msg.content {
        Some(sapient_message::Content::Task(t)) => t.task_id.clone(),
        _ => None,
    }
}

pub fn node_id_of(msg: &SapientMessage) -> Option<String> {
    msg.node_id.clone()
}

/// The heartbeat period a registering peer declared, so the DMM side can report it.
pub fn declared_status_interval(msg: &SapientMessage) -> Option<Duration> {
    let Some(sapient_message::Content::Registration(reg)) = &msg.content else {
        return None;
    };
    let d = reg.status_definition.as_ref()?.status_interval.as_ref()?;
    let value = d.value? as f64;
    let secs = match registration::TimeUnits::try_from(d.units.unwrap_or(0)).ok()? {
        registration::TimeUnits::Nanoseconds => value / 1e9,
        registration::TimeUnits::Microseconds => value / 1e6,
        registration::TimeUnits::Milliseconds => value / 1e3,
        registration::TimeUnits::Seconds => value,
        registration::TimeUnits::Minutes => value * 60.0,
        registration::TimeUnits::Hours => value * 3600.0,
        registration::TimeUnits::Days => value * 86_400.0,
        registration::TimeUnits::Unspecified => return None,
    };
    // The value is the peer's to choose, so it is checked before it becomes a Duration:
    // from_secs_f64 panics on infinity or a value past u64 seconds, and that panic would
    // kill the session task. A heartbeat slower than once a day is not one anyone meant,
    // so it is reported as absent rather than trusted.
    if !secs.is_finite() || secs <= 0.0 || secs > MAX_DECLARED_INTERVAL_SECS {
        return None;
    }
    Duration::try_from_secs_f64(secs).ok()
}

/// The longest heartbeat period a peer may declare and have it reported: one day.
const MAX_DECLARED_INTERVAL_SECS: f64 = 86_400.0;

/// What a relay may put on a SAPIENT network, decided by the role this arm holds, and the
/// message as it goes out.
///
/// A relayed sample is only ever something this arm could say in its role: the sensor side
/// reports (`DetectionReport`, `Alert`), the controller side directs (`Task`, `AlertAck`).
/// Anything else — a `Registration`, a heartbeat, an acknowledgement — would be a
/// translator speaking for the session, and the session is this arm's alone.
///
/// The envelope is rewritten, because on the wire the message is this arm's: its node id,
/// and the time it is sent. A controller's direction is addressed to the sensor that
/// registered with it.
pub fn relay_for_role(
    bytes: &[u8],
    role: Role,
    node_id: &str,
    registered_peer: Option<&str>,
) -> std::result::Result<SapientMessage, String> {
    use sapient_message::Content as C;
    let mut msg = SapientMessage::decode(bytes).map_err(|_| "not a SapientMessage".to_string())?;
    let allowed = matches!(
        (role, &msg.content),
        (Role::Asm, Some(C::DetectionReport(_)) | Some(C::Alert(_)))
            | (
                Role::Dmm | Role::Client,
                Some(C::Task(_)) | Some(C::AlertAck(_))
            )
    );
    if !allowed {
        return Err(format!(
            "a {} may not be relayed by a node in the {role} role",
            content_name(&msg)
        ));
    }
    // A handler serves many nodes, so a client cannot address a message on anyone's
    // behalf: it goes where its source said, or not at all.
    if role == Role::Client && msg.destination_id.as_deref().unwrap_or("").is_empty() {
        return Err(format!(
            "a {} relayed to a message handler must name the node it is for",
            content_name(&msg)
        ));
    }
    msg.node_id = Some(node_id.to_string());
    msg.timestamp = Some(now());
    if role == Role::Dmm {
        if let Some(sensor) = registered_peer {
            msg.destination_id = Some(sensor.to_string());
        }
    }
    Ok(msg)
}

/// Which variant this message carries, for a log line or a session record.
pub fn content_name(msg: &SapientMessage) -> &'static str {
    use sapient_message::Content as C;
    match &msg.content {
        Some(C::Registration(_)) => "registration",
        Some(C::RegistrationAck(_)) => "registration_ack",
        Some(C::StatusReport(_)) => "status_report",
        Some(C::DetectionReport(_)) => "detection_report",
        Some(C::Task(_)) => "task",
        Some(C::TaskAck(_)) => "task_ack",
        Some(C::Alert(_)) => "alert",
        Some(C::AlertAck(_)) => "alert_ack",
        Some(C::Error(_)) => "error",
        None => "empty",
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::Duration;

    fn relayed(content: sapient_message::Content) -> Vec<u8> {
        SapientMessage {
            timestamp: None,
            node_id: Some("translator".into()),
            destination_id: None,
            content: Some(content),
            additional_information: None,
        }
        .encode_to_vec()
    }

    #[test]
    fn a_sensor_side_node_relays_reports_under_its_own_node_id_and_nothing_else() {
        use crate::proto::v2::{Alert, DetectionReport, Task};
        use sapient_message::Content as C;
        let out = relay_for_role(
            &relayed(C::DetectionReport(DetectionReport::default())),
            Role::Asm,
            "pod-node",
            None,
        )
        .unwrap();
        assert_eq!(out.node_id.as_deref(), Some("pod-node"));
        assert!(out.timestamp.is_some(), "the send time is the arm's");
        assert!(relay_for_role(&relayed(C::Alert(Alert::default())), Role::Asm, "n", None).is_ok());
        for refused in [
            C::Registration(Registration::default()),
            C::StatusReport(StatusReport::default()),
            C::RegistrationAck(RegistrationAck::default()),
            C::Task(Task::default()),
        ] {
            let err = relay_for_role(&relayed(refused), Role::Asm, "n", None).unwrap_err();
            assert!(err.contains("asm role"), "{err}");
        }
    }

    #[test]
    fn a_controller_side_node_relays_direction_addressed_to_its_sensor() {
        use crate::proto::v2::{AlertAck, DetectionReport, Task};
        use sapient_message::Content as C;
        let out = relay_for_role(
            &relayed(C::Task(Task::default())),
            Role::Dmm,
            "pod-node",
            Some("sensor-1"),
        )
        .unwrap();
        assert_eq!(out.destination_id.as_deref(), Some("sensor-1"));
        assert_eq!(out.node_id.as_deref(), Some("pod-node"));
        assert!(relay_for_role(
            &relayed(C::AlertAck(AlertAck::default())),
            Role::Dmm,
            "n",
            None
        )
        .is_ok());
        assert!(relay_for_role(
            &relayed(C::DetectionReport(DetectionReport::default())),
            Role::Dmm,
            "n",
            None
        )
        .is_err());
    }

    #[test]
    fn a_sample_that_is_not_a_sapient_message_is_refused() {
        assert!(relay_for_role(b"\xff\xff\xff\xff not protobuf", Role::Asm, "n", None).is_err());
        // Decodes as protobuf, but carries no content an arm could send.
        assert!(relay_for_role(b"", Role::Asm, "n", None).is_err());
    }

    fn cfg() -> PeerConfig {
        PeerConfig {
            label: "sapient".into(),
            target: Some("10.0.0.1:48000".into()),
            listen: None,
            role: crate::session::Role::Asm,
            framing: None,
            node_id: "1c4a1f0e-0000-4000-8000-000000000000".into(),
            icd_version: "BSI Flex 335 v2.0".into(),
            node_type: node_type("OTHER").unwrap(),
            capabilities: vec![],
            status_interval: Duration::from_secs(5),
            connect_timeout: Duration::from_secs(10),
            write_timeout: Duration::from_secs(10),
            role_probe: Duration::from_secs(3),
            client_idle: Duration::ZERO,
            mirror_egress: true,
            audit: None,
        }
    }

    #[test]
    fn node_types_parse_with_or_without_the_prefix() {
        assert_eq!(
            node_type("RADAR").unwrap(),
            node_type("NODE_TYPE_RADAR").unwrap()
        );
        assert_eq!(node_type("other").unwrap(), node_type("OTHER").unwrap());
        let err = format!("{:#}", node_type("TELEPATHIC").unwrap_err());
        assert!(err.contains("RADAR"), "{err}");
    }

    #[test]
    fn capabilities_parse_all_four_fields() {
        let caps = capabilities("Radar:Maximum Transmit Power:50:dB, Relay:Feed").unwrap();
        assert_eq!(caps.len(), 2);
        assert_eq!(caps[0].0, "Radar");
        assert_eq!(caps[0].2.as_deref(), Some("50"));
        assert_eq!(caps[0].3.as_deref(), Some("dB"));
        assert_eq!(caps[1].2, None);
        assert!(capabilities("").unwrap().is_empty());
        assert!(capabilities("lonely").is_err());
    }

    #[test]
    fn the_registration_carries_every_mandatory_field() {
        let msg = registration(&cfg());
        let Some(sapient_message::Content::Registration(reg)) = &msg.content else {
            panic!("not a registration");
        };
        assert!(
            msg.timestamp.is_some(),
            "the envelope timestamp is mandatory"
        );
        assert!(msg.node_id.is_some(), "the envelope node id is mandatory");
        assert_eq!(reg.node_definition.len(), 1);
        assert!(reg.icd_version.is_some());
        assert!(
            !reg.capabilities.is_empty(),
            "an empty capability list claims nothing"
        );
        assert!(reg
            .status_definition
            .as_ref()
            .unwrap()
            .status_interval
            .is_some());
        let mode = &reg.mode_definition[0];
        assert!(mode.mode_name.is_some() && mode.mode_type.is_some());
        assert!(mode.settle_time.is_some(), "settle_time is mandatory");
        assert!(
            mode.task.as_ref().unwrap().region_definition.is_some(),
            "a mode's task definition needs a region definition"
        );
        assert_eq!(reg.config_data[0].manufacturer, MANUFACTURER);
    }

    // Guard the production registration against the pinned reference validator.
    // Real-controller interoperability still requires a separate test.
    #[test]
    fn registration_is_accepted_by_the_reference_validator() {
        let msg = registration(&cfg());
        let violations = sapient_receiver::validate::violations(&msg);
        assert!(
            violations.is_empty(),
            "the reference validator rejects this arm's registration:\n- {}",
            violations.join("\n- ")
        );
    }

    // Check the production heartbeat against the same reference validator.
    #[test]
    fn status_report_is_accepted_by_the_reference_validator() {
        let msg = status_report(&cfg().node_id);
        let violations = sapient_receiver::validate::violations(&msg);
        assert!(
            violations.is_empty(),
            "the reference validator rejects this arm's heartbeat:\n- {}",
            violations.join("\n- ")
        );
    }

    #[test]
    fn a_declared_capability_replaces_the_default_one() {
        let mut c = cfg();
        c.capabilities = capabilities("Radar:Range:5:km").unwrap();
        let msg = registration(&c);
        let Some(sapient_message::Content::Registration(reg)) = &msg.content else {
            unreachable!()
        };
        assert_eq!(reg.capabilities.len(), 1);
        assert_eq!(reg.capabilities[0].category.as_deref(), Some("Radar"));
    }

    #[test]
    fn frames_round_trip_in_both_byte_orders() {
        for order in [ByteOrder::Le, ByteOrder::Be] {
            let msg = registration(&cfg());
            let wire = encode(&msg, order);
            let len = match order {
                ByteOrder::Le => u32::from_le_bytes(wire[..4].try_into().unwrap()),
                ByteOrder::Be => u32::from_be_bytes(wire[..4].try_into().unwrap()),
            } as usize;
            assert_eq!(len, wire.len() - 4, "the prefix must be the body length");
            let back = decode(&wire[4..]).expect("decodes");
            assert_eq!(back.node_id, msg.node_id);
            assert!(is_registration(&back));
        }
    }

    #[test]
    fn noise_does_not_look_like_a_message() {
        assert!(!decodes(&[0xff, 0xff, 0xff, 0xff]));
        assert!(!decodes(b"<event/>"));
        let wire = encode(&status_report("node"), ByteOrder::Le);
        assert!(decodes(&wire[4..]));
    }

    #[test]
    fn the_heartbeat_is_a_complete_status_report() {
        let msg = status_report("node-1");
        let Some(sapient_message::Content::StatusReport(r)) = &msg.content else {
            panic!("not a status report");
        };
        assert_eq!(r.report_id.as_ref().unwrap().len(), 26, "a ULID");
        assert_eq!(r.system, Some(status_report::System::Ok as i32));
        assert!(r.info.is_some() && r.mode.is_some());
        assert_eq!(content_name(&msg), "status_report");
    }

    #[test]
    fn an_acknowledgement_is_read_with_its_reasons() {
        let mut ack = registration_ack("me", Some("them".into()), false);
        let Some(sapient_message::Content::RegistrationAck(inner)) = &mut ack.content else {
            unreachable!()
        };
        inner.ack_response_reason = vec!["icd_version".into(), "capabilities".into()];
        let (accepted, reasons) = registration_ack_of(&ack).unwrap();
        assert!(!accepted);
        assert_eq!(reasons.len(), 2);
        assert_eq!(ack.destination_id.as_deref(), Some("them"));
    }

    #[test]
    fn a_task_is_always_answered() {
        let ack = task_ack("me", "01J0000000000000000000000A", "no tasking");
        let Some(sapient_message::Content::TaskAck(inner)) = &ack.content else {
            panic!("not a task ack");
        };
        assert_eq!(
            inner.task_status,
            Some(task_ack::TaskStatus::Rejected as i32)
        );
        assert_eq!(inner.reason.len(), 1);
    }

    #[test]
    fn a_peers_declared_interval_is_read_in_its_own_units() {
        let mut c = cfg();
        c.status_interval = Duration::from_secs(30);
        let msg = registration(&c);
        assert_eq!(
            declared_status_interval(&msg),
            Some(Duration::from_secs(30))
        );
        // A peer that speaks in milliseconds means the same thing.
        let mut other = registration(&c);
        if let Some(sapient_message::Content::Registration(reg)) = &mut other.content {
            reg.status_definition = Some(registration::StatusDefinition {
                status_interval: Some(registration::Duration {
                    units: Some(registration::TimeUnits::Milliseconds as i32),
                    value: Some(1500.0),
                }),
                ..Default::default()
            });
        }
        assert_eq!(
            declared_status_interval(&other),
            Some(Duration::from_millis(1500))
        );
    }

    #[test]
    fn a_declared_interval_no_one_could_mean_is_reported_as_absent() {
        // These values are outside the supported interval, including values that panic
        // Duration::from_secs_f64.
        let poisoned = [
            (registration::TimeUnits::Seconds, f32::MAX),
            (registration::TimeUnits::Seconds, f32::INFINITY),
            (registration::TimeUnits::Seconds, f32::NAN),
            (registration::TimeUnits::Days, 3.0e14),
            (registration::TimeUnits::Seconds, -5.0),
            (registration::TimeUnits::Seconds, 0.0),
            (registration::TimeUnits::Days, 2.0),
        ];
        for (units, value) in poisoned {
            let mut msg = registration(&cfg());
            if let Some(sapient_message::Content::Registration(reg)) = &mut msg.content {
                reg.status_definition = Some(registration::StatusDefinition {
                    status_interval: Some(registration::Duration {
                        units: Some(units as i32),
                        value: Some(value),
                    }),
                    ..Default::default()
                });
            }
            assert_eq!(
                declared_status_interval(&msg),
                None,
                "{units:?} {value} should not be trusted"
            );
        }
    }
}
