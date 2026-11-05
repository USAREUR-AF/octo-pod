//! SAPIENT test receiver with validation ported from the pinned reference harness.
mod framing;
pub mod json;
mod respond;
pub mod validate;

use anyhow::{ensure, Context, Result};
use prost::Message;
use sapient_proto::v2::{sapient_message::Content, SapientMessage};
use std::time::Duration;
use tokio::net::TcpStream;
use tokio::time::{interval_at, timeout, Instant, MissedTickBehavior};
use tracing::{info, warn};

/// One accepted connection: read frames until the peer closes, judging and logging each
/// and acknowledging a registration.
pub async fn session(
    sock: TcpStream,
    our_id: &str,
    name: &str,
    strict: bool,
    status_interval: Duration,
) -> Result<()> {
    ensure!(
        !status_interval.is_zero(),
        "status interval must be positive"
    );
    let peer = sock.peer_addr().context("peer_addr")?;
    info!(%peer, name, "CONNECT");

    let (mut reader, mut writer) = sock.into_split();
    let mut registered = None::<String>;
    let mut heartbeat = interval_at(Instant::now() + status_interval, status_interval);
    heartbeat.set_missed_tick_behavior(MissedTickBehavior::Skip);
    loop {
        // Keep the read future alive across heartbeat ticks: cancelling read_exact
        // halfway through a prefix would desynchronise the stream.
        let frame = framing::read_frame(&mut reader);
        tokio::pin!(frame);
        let body = loop {
            tokio::select! {
                result = &mut frame => break result?,
                _ = heartbeat.tick(), if registered.is_some() => {
                    let msg = respond::status_report(our_id, registered.as_deref().unwrap());
                    timeout(Duration::from_secs(5), framing::write_frame(&mut writer, &msg.encode_to_vec()))
                        .await.context("status write timed out")??;
                }
            }
        };
        let Some(body) = body else {
            info!(%peer, name, "peer closed");
            return Ok(());
        };

        let msg = match SapientMessage::decode(&*body) {
            Ok(m) => m,
            Err(e) => {
                warn!(%peer, name, "undecodable frame ({} bytes): {e}", body.len());
                continue;
            }
        };

        let kind = content_kind(&msg);
        let violations = validate::violations(&msg);
        if violations.is_empty() {
            info!(%peer, name, kind, "VALID");
        } else {
            warn!(%peer, name, kind, "INVALID: {}", violations.join("; "));
        }

        // Act as a fusion node: a registration is acknowledged. In strict mode a
        // non-conforming one is rejected with its reasons; in lenient mode accepted.
        if let Some(Content::Registration(_)) = &msg.content {
            let their_id = msg.node_id.clone().unwrap_or_default();
            let ack = respond::registration_ack(our_id, &their_id, &violations, strict);
            timeout(
                Duration::from_secs(5),
                framing::write_frame(&mut writer, &ack.encode_to_vec()),
            )
            .await
            .context("ack write timed out")??;
            let accepted = !strict || violations.is_empty();
            info!(%peer, name, accepted, "sent RegistrationAck");
            if !accepted {
                return Ok(());
            }
            registered = Some(their_id);
            heartbeat.reset();
        }
    }
}

fn content_kind(m: &SapientMessage) -> &'static str {
    match &m.content {
        Some(Content::Registration(_)) => "Registration",
        Some(Content::RegistrationAck(_)) => "RegistrationAck",
        Some(Content::StatusReport(_)) => "StatusReport",
        Some(Content::DetectionReport(_)) => "DetectionReport",
        Some(_) => "other",
        None => "empty",
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use tokio::io::AsyncWriteExt;
    use tokio::net::TcpListener;

    const OURS: &str = "00000000-0000-4000-8000-00000000aaaa";
    const THEIRS: &str = "00000000-0000-4000-8000-00000000bbbb";

    async fn receive(sock: &mut TcpStream) -> SapientMessage {
        let body = timeout(Duration::from_secs(2), framing::read_frame(sock))
            .await
            .unwrap()
            .unwrap()
            .unwrap();
        SapientMessage::decode(body.as_slice()).unwrap()
    }

    #[tokio::test]
    async fn strict_acceptance_heartbeats_and_fragmented_frames_coexist() {
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let mut client = TcpStream::connect(listener.local_addr().unwrap())
            .await
            .unwrap();
        let server = tokio::spawn(async move {
            let (sock, _) = listener.accept().await.unwrap();
            session(sock, OURS, "test", true, Duration::from_millis(20)).await
        });
        // No unsolicited status before an accepted registration.
        assert!(
            timeout(Duration::from_millis(60), framing::read_frame(&mut client))
                .await
                .is_err()
        );
        let mut registration = crate::json::decode(include_str!(
            "../tests/vectors/true/Registration.FollowObject.json"
        ))
        .unwrap();
        registration.node_id = Some(THEIRS.into());
        registration.destination_id = Some(OURS.into());
        assert!(validate::violations(&registration).is_empty());
        let registration = registration.encode_to_vec();
        framing::write_frame(&mut client, &registration)
            .await
            .unwrap();
        let ack = receive(&mut client).await;
        assert!(
            matches!(ack.content, Some(Content::RegistrationAck(a)) if a.acceptance == Some(true))
        );
        // Pause in the middle of a prefix while the receiver sends several statuses.
        let prefix = (registration.len() as u32).to_le_bytes();
        client.write_all(&prefix[..1]).await.unwrap();
        let mut ids = std::collections::HashSet::new();
        for _ in 0..3 {
            let status = receive(&mut client).await;
            assert_eq!(status.node_id.as_deref(), Some(OURS));
            assert_eq!(status.destination_id.as_deref(), Some(THEIRS));
            assert!(status.timestamp.is_some());
            assert!(validate::violations(&status).is_empty());
            let Some(Content::StatusReport(s)) = status.content else {
                panic!("expected heartbeat")
            };
            let id = s.report_id.unwrap();
            assert_eq!(id.len(), 26);
            assert!(id
                .bytes()
                .all(|b| b"0123456789ABCDEFGHJKMNPQRSTVWXYZ".contains(&b)));
            assert!(ids.insert(id));
            assert_eq!(s.system, Some(1));
            assert!(s.info.is_some());
            assert_eq!(s.mode.as_deref(), Some("receiver"));
        }
        client.write_all(&prefix[1..]).await.unwrap();
        client.write_all(&registration).await.unwrap();
        timeout(Duration::from_secs(2), async {
            loop {
                if let Some(Content::RegistrationAck(a)) = receive(&mut client).await.content {
                    assert_eq!(a.acceptance, Some(true));
                    break;
                }
            }
        })
        .await
        .expect("fragmented registration still decodes after heartbeat ticks");
        // Half-close our writes, then drain any in-flight heartbeats. Dropping a
        // socket with unread data sends RST on Linux and masks successful framing
        // with a platform-dependent teardown error in the server task.
        client.shutdown().await.unwrap();
        timeout(Duration::from_secs(2), async {
            while framing::read_frame(&mut client).await.unwrap().is_some() {}
        })
        .await
        .expect("receiver closes after the client half-closes");
        timeout(Duration::from_secs(2), server)
            .await
            .unwrap()
            .unwrap()
            .unwrap();
    }
}
