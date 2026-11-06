//! One SAPIENT message body, as three consumers read it:
//!
//! - the sapient arm's session (`sapient/src/session.rs`): `wire::decode`, then the fields
//!   `on_message` acts on, including the status interval a registering peer declares;
//! - the sapient arm's relay: a sample from the pen becomes a message on the peer's
//!   network through `wire::relay_for_role`, which must only let out what the arm's role
//!   may say, under the arm's own node id;
//! - the SAPIENT test receiver (`sapient_receiver::session`): decode, then judge the
//!   message with `validate::violations`, which walks every field by reflection.

#![no_main]

use std::time::Duration;

use libfuzzer_sys::fuzz_target;
use prost::Message;
use sapient::framing::ByteOrder;
use sapient::proto::v2::sapient_message::Content;
use sapient::session::Role;
use sapient::wire;
use sapient_receiver::validate;

const NODE: &str = "00000000-0000-4000-8000-00000000aaaa";
const SENSOR: &str = "00000000-0000-4000-8000-00000000bbbb";
/// The slowest heartbeat a peer may declare and have reported, one day (`wire.rs`).
const MAX_INTERVAL_SECS: u64 = 86_400;

fuzz_target!(|data: &[u8]| {
    let decoded = wire::decode(data);

    // The session's inbound path.
    if let Some(msg) = &decoded {
        let _ = wire::is_registration(msg);
        let _ = wire::node_id_of(msg);
        let _ = wire::content_name(msg);
        let _ = wire::task_id_of(msg);
        let _ = wire::registration_ack_of(msg);
        if let Some(interval) = wire::declared_status_interval(msg) {
            assert!(
                !interval.is_zero() && interval <= Duration::from_secs(MAX_INTERVAL_SECS),
                "a declared interval outside (0, 1 day] was reported: {interval:?}"
            );
        }
        let has_header = msg.content.is_some() || msg.node_id.is_some() || msg.timestamp.is_some();
        assert_eq!(wire::decodes(data), has_header);

        // The test receiver's per-frame path.
        let _ = validate::violations(msg);
    } else {
        assert!(
            !wire::decodes(data),
            "decodes() accepted bytes decode() refuses"
        );
    }

    // The relay path, for every role and with and without a registered sensor.
    for role in [Role::Asm, Role::Dmm, Role::Client, Role::Auto] {
        for peer in [Some(SENSOR), None] {
            let Ok(out) = wire::relay_for_role(data, role, NODE, peer) else {
                continue;
            };
            let allowed = match role {
                Role::Asm => matches!(
                    out.content,
                    Some(Content::DetectionReport(_) | Content::Alert(_))
                ),
                Role::Dmm | Role::Client => {
                    matches!(out.content, Some(Content::Task(_) | Content::AlertAck(_)))
                }
                Role::Auto => false,
            };
            assert!(allowed, "a {role} relayed a {}", wire::content_name(&out));
            assert_eq!(
                out.node_id.as_deref(),
                Some(NODE),
                "relayed under another node id"
            );
            assert!(out.timestamp.is_some(), "relayed without a send time");
            let received = decoded.as_ref().and_then(|m| m.destination_id.as_deref());
            match (role, peer) {
                (Role::Dmm, Some(sensor)) => {
                    assert_eq!(out.destination_id.as_deref(), Some(sensor));
                }
                (Role::Client, _) => assert!(
                    !out.destination_id.as_deref().unwrap_or_default().is_empty(),
                    "a client relayed a message with no destination"
                ),
                _ => assert_eq!(out.destination_id.as_deref(), received),
            }

            // What goes on the wire reads back as the message that was meant.
            for order in [ByteOrder::Le, ByteOrder::Be] {
                let framed = wire::encode(&out, order);
                let Some((&[a, b, c, d], body)) = framed.split_first_chunk::<4>() else {
                    panic!("a framed message is shorter than its prefix");
                };
                let len = match order {
                    ByteOrder::Le => u32::from_le_bytes([a, b, c, d]),
                    ByteOrder::Be => u32::from_be_bytes([a, b, c, d]),
                };
                assert_eq!(
                    usize::try_from(len).ok(),
                    Some(body.len()),
                    "prefix and body disagree"
                );
                let back = wire::decode(body).map(|m| m.encode_to_vec());
                assert_eq!(
                    back,
                    Some(out.encode_to_vec()),
                    "the relayed message does not read back"
                );
            }
        }
    }
});
