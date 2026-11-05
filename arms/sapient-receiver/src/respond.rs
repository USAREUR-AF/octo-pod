//! What the receiver sends back. A fusion node is not a passive sniffer: a registering
//! node waits for a RegistrationAck and will not proceed to status and detections without
//! one. Answering is what makes this a receiver a real node can register with, and what
//! sets it apart from a validate-and-log harness.

use std::time::{SystemTime, UNIX_EPOCH};

use sapient_proto::v2::{
    sapient_message::Content, status_report, RegistrationAck, SapientMessage, StatusReport,
};

/// Build the acknowledgement for a node that just registered.
///
/// `our_id` is this receiver's own node UUID, `their_id` the registering node's (copied
/// from its envelope, where the ack's `destination_id` must point). `violations` is what
/// the registration broke, if anything. `strict` decides policy: strict REJECTS a
/// non-conforming registration and returns the reasons (a by-the-book controller);
/// lenient accepts regardless and the reasons are only logged by the caller (the lab
/// stand-in). A conforming registration is accepted either way.
pub fn registration_ack(
    our_id: &str,
    their_id: &str,
    violations: &[String],
    strict: bool,
) -> SapientMessage {
    let reject = strict && !violations.is_empty();
    SapientMessage {
        timestamp: Some(now()),
        node_id: Some(our_id.to_string()),
        destination_id: Some(their_id.to_string()),
        content: Some(Content::RegistrationAck(RegistrationAck {
            acceptance: Some(!reject),
            // The standard requires a rejection to carry its reasons; an acceptance
            // carries none.
            ack_response_reason: if reject {
                violations.to_vec()
            } else {
                Vec::new()
            },
        })),
        additional_information: None,
    }
}

/// A controller heartbeat addressed to the accepted node.
pub fn status_report(our_id: &str, their_id: &str) -> SapientMessage {
    let timestamp = now();
    // Same ULID layout as the arm: 48 timestamp bits followed by 80 entropy bits.
    let millis = timestamp.seconds as u64 * 1000 + timestamp.nanos as u64 / 1_000_000;
    let entropy = uuid::Uuid::new_v4();
    let mut bits = [0u8; 16];
    bits[..6].copy_from_slice(&millis.to_be_bytes()[2..]);
    bits[6..].copy_from_slice(&entropy.as_bytes()[..10]);
    let mut value = u128::from_be_bytes(bits);
    let mut id = [b'0'; 26];
    for slot in id.iter_mut().rev() {
        *slot = b"0123456789ABCDEFGHJKMNPQRSTVWXYZ"[(value & 31) as usize];
        value >>= 5;
    }
    SapientMessage {
        timestamp: Some(timestamp),
        node_id: Some(our_id.into()),
        destination_id: Some(their_id.into()),
        content: Some(Content::StatusReport(StatusReport {
            report_id: Some(String::from_utf8(id.to_vec()).expect("ASCII ULID")),
            system: Some(status_report::System::Ok as i32),
            info: Some(status_report::Info::Unchanged as i32),
            mode: Some("receiver".into()),
            ..Default::default()
        })),
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

#[cfg(test)]
mod tests {
    use super::*;

    const OURS: &str = "00000000-0000-4000-8000-00000000aaaa";
    const THEIRS: &str = "00000000-0000-4000-8000-00000000bbbb";

    fn ack(msg: &SapientMessage) -> &RegistrationAck {
        match &msg.content {
            Some(Content::RegistrationAck(a)) => a,
            _ => panic!("not an ack"),
        }
    }

    #[test]
    fn conforming_is_accepted_either_mode() {
        for strict in [true, false] {
            let m = registration_ack(OURS, THEIRS, &[], strict);
            assert_eq!(ack(&m).acceptance, Some(true));
            assert!(ack(&m).ack_response_reason.is_empty());
            assert_eq!(m.destination_id.as_deref(), Some(THEIRS));
        }
    }

    #[test]
    fn strict_rejects_with_reasons() {
        let reasons = vec!["region_area must not be empty".to_string()];
        let m = registration_ack(OURS, THEIRS, &reasons, true);
        assert_eq!(ack(&m).acceptance, Some(false));
        assert_eq!(ack(&m).ack_response_reason, reasons);
    }

    #[test]
    fn lenient_accepts_despite_violations() {
        let reasons = vec!["region_area must not be empty".to_string()];
        let m = registration_ack(OURS, THEIRS, &reasons, false);
        assert_eq!(ack(&m).acceptance, Some(true));
        assert!(ack(&m).ack_response_reason.is_empty());
    }
}
