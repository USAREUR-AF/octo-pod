// SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
// SPDX-License-Identifier: Apache-2.0
//! Records what the landing page's hero replays. Starts a Zenoh router on loopback
//! (the arms' own test pen), runs two copies of the real `udp-ingest` arm against it,
//! sends synthetic datagrams to both, and writes every send, every publication the
//! router saw and every stats line the arms logged, with times, as JSON.
//!
//! `sensor1` accepts its sender (allow 127.0.0.1). `sensor2` allows only 192.0.2.30,
//! so the same datagrams from 127.0.0.1 are dropped at its gate and never published.
//!
//!   cargo run --release -- <path to udp-ingest binary> <output.json>

use std::process::Stdio;
use std::time::{Duration, Instant};

use octo_arm_common::testing::{free_udp_port, TestPen};
use serde_json::{json, Value};
use tokio::io::{AsyncBufReadExt, BufReader};
use tokio::process::Command;
use tokio::sync::mpsc;

#[tokio::main]
async fn main() {
    let args: Vec<String> = std::env::args().collect();
    let (arm_bin, out) = (args[1].clone(), args[2].clone());
    let t0 = Instant::now();
    let ms = move || t0.elapsed().as_millis() as u64;

    let pen = TestPen::start().await;
    let sub = pen.subscribe("raw/**").await;
    let (log_tx, mut log_rx) = mpsc::unbounded_channel::<(String, String)>();

    let mut arms = Vec::new();
    let mut ports = Vec::new();
    for (name, allow) in [("sensor1", "127.0.0.1"), ("sensor2", "192.0.2.30")] {
        let port = free_udp_port();
        ports.push(port);
        let mut child = Command::new(&arm_bin)
            .args(["--feeds", &format!("feed1={port}"), "--bind-host", "127.0.0.1"])
            .args(["--key-prefix", &format!("raw/{name}"), "--endpoint", &pen.endpoint])
            .args(["--allow-sources", allow, "--stats-secs", "2"])
            .env("NO_COLOR", "1")
            .env("RUST_LOG", "info")
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .kill_on_drop(true)
            .spawn()
            .expect("start udp-ingest");
        for stream in [
            Box::new(child.stdout.take().unwrap()) as Box<dyn tokio::io::AsyncRead + Unpin + Send>,
            Box::new(child.stderr.take().unwrap()),
        ] {
            let tx = log_tx.clone();
            let name = name.to_string();
            tokio::spawn(async move {
                let mut lines = BufReader::new(stream).lines();
                while let Ok(Some(line)) = lines.next_line().await {
                    let _ = tx.send((name.clone(), line));
                }
            });
        }
        arms.push(child);
    }

    let mut events: Vec<Value> = Vec::new();
    let mut logs: Vec<Value> = Vec::new();
    // Wait until both arms say their listener is bound.
    let mut bound = 0;
    while bound < 2 {
        let (name, line) = tokio::time::timeout(Duration::from_secs(30), log_rx.recv())
            .await
            .expect("arm did not start")
            .expect("arm log closed");
        if line.contains("udp listener bound") {
            bound += 1;
        }
        logs.push(json!({"t": ms(), "arm": name, "line": line}));
    }

    let tx = std::net::UdpSocket::bind("127.0.0.1:0").expect("sender");
    let sent_at = ms();
    for seq in 1..=24u32 {
        let target = if seq % 4 == 0 { 1 } else { 0 };
        let payload = format!("synthetic seq={seq:04} t={:06}ms", ms() - sent_at);
        tx.send_to(payload.as_bytes(), ("127.0.0.1", ports[target])).expect("send");
        let to = ["sensor1", "sensor2"][target];
        events.push(json!({"t": ms(), "kind": "sent", "to": to,
                           "bytes": payload.len(), "payload": payload}));
        let until = Instant::now() + Duration::from_millis(250);
        while Instant::now() < until {
            tokio::select! {
                s = sub.recv_async() => {
                    let s = s.expect("subscriber");
                    let bytes = s.payload().to_bytes();
                    events.push(json!({"t": ms(), "kind": "published", "key": s.key_expr().as_str(),
                                       "bytes": bytes.len(), "payload": String::from_utf8_lossy(&bytes)}));
                }
                l = log_rx.recv() => {
                    if let Some((name, line)) = l { logs.push(json!({"t": ms(), "arm": name, "line": line})); }
                }
                _ = tokio::time::sleep_until(until.into()) => {}
            }
        }
    }
    // Let the last publications land and one more stats line print from each arm.
    let until = Instant::now() + Duration::from_millis(4500);
    while Instant::now() < until {
        tokio::select! {
            s = sub.recv_async() => {
                let s = s.expect("subscriber");
                let bytes = s.payload().to_bytes();
                events.push(json!({"t": ms(), "kind": "published", "key": s.key_expr().as_str(),
                                   "bytes": bytes.len(), "payload": String::from_utf8_lossy(&bytes)}));
            }
            l = log_rx.recv() => {
                if let Some((name, line)) = l { logs.push(json!({"t": ms(), "arm": name, "line": line})); }
            }
            _ = tokio::time::sleep_until(until.into()) => {}
        }
    }
    for mut a in arms {
        let _ = a.kill().await;
    }
    let doc = json!({"router": "loopback test pen (octo_arm_common::testing::TestPen)",
                     "arms": [{"name": "sensor1", "allow_sources": "127.0.0.1"},
                              {"name": "sensor2", "allow_sources": "192.0.2.30"}],
                     "events": events, "logs": logs});
    std::fs::write(&out, serde_json::to_string_pretty(&doc).unwrap() + "\n").expect("write");
    eprintln!("wrote {out}");
}
