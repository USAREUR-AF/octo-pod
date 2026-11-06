// SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
// SPDX-License-Identifier: Apache-2.0
//! Writes the seed inputs in `seeds/`, one directory per fuzz target.
//!
//! Run it from `arms/fuzz` after a change to the reference vectors or to a target's input
//! format, and commit the result:
//!
//! ```text
//! cargo run --example make_seeds
//! ```
//!
//! The SAPIENT messages are the binary encodings of the reference vectors in
//! `../sapient-receiver/tests/vectors/true` (Crown copyright, Apache-2.0; see
//! `seeds/NOTICE`). Everything else is synthetic and uses documentation addresses only.
//! `sapient_json` has no directory here: it starts from the reference vectors directly.

use std::error::Error;
use std::fs;
use std::path::Path;

use octo_arm_common::framing::Framing;
use prost::Message;
use sapient::framing::ByteOrder;
use sapient::proto::v2::SapientMessage;
use sapient::wire;
use sapient_receiver::json;

type Result<T> = std::result::Result<T, Box<dyn Error>>;

/// The targets that have a directory in `seeds/`.
const TARGETS: [&str; 4] = [
    "tcp_frame",
    "sapient_frame",
    "sapient_message",
    "downlink_gate",
];

/// The selector byte that `tcp_frame` reads: mode in the low two bits, read size above.
fn tcp_selector(framing: Framing, read_size: u8) -> u8 {
    let mode = match framing {
        Framing::Stream => 0,
        Framing::Newline => 1,
        Framing::Len32Be => 2,
        Framing::Len32Le => 3,
    };
    ((read_size - 1) << 2) | mode
}

/// The selector byte that `sapient_frame` reads: detect, little-endian or big-endian by
/// remainder, read size by quotient.
fn sapient_selector(pinned: Option<ByteOrder>, read_size: u8) -> u8 {
    let mode = match pinned {
        None => 0,
        Some(ByteOrder::Le) => 1,
        Some(ByteOrder::Be) => 2,
    };
    (read_size - 1) * 3 + mode
}

/// `Registration.FollowObject` becomes `registration_follow_object`.
fn snake_case(stem: &str) -> String {
    let mut out = String::with_capacity(stem.len() + 8);
    let mut previous = '_';
    for c in stem.chars() {
        if c == '.' {
            out.push('_');
        } else if c.is_ascii_uppercase()
            && (previous.is_ascii_lowercase() || previous.is_ascii_digit())
        {
            out.push('_');
            out.push(c.to_ascii_lowercase());
        } else {
            out.push(c.to_ascii_lowercase());
        }
        previous = c;
    }
    out
}

fn write(dir: &Path, name: &str, bytes: &[u8]) -> Result<()> {
    fs::create_dir_all(dir)?;
    fs::write(dir.join(name), bytes)?;
    Ok(())
}

fn with_selector(selector: u8, stream: &[u8]) -> Vec<u8> {
    let mut out = Vec::with_capacity(stream.len() + 1);
    out.push(selector);
    out.extend_from_slice(stream);
    out
}

/// A message whose encoding is exactly `len` bytes: a node id, padded through
/// `destination_id`.
fn message_of_length(len: usize) -> Result<SapientMessage> {
    for pad in 0..len {
        let msg = SapientMessage {
            node_id: Some("00000000-0000-4000-8000-00000000aaaa".into()),
            destination_id: Some("0".repeat(pad)),
            ..SapientMessage::default()
        };
        if msg.encoded_len() == len {
            return Ok(msg);
        }
    }
    Err(format!("no padding gives a {len}-byte message").into())
}

/// The reference vectors, decoded, each written as a `sapient_message` seed.
fn reference_messages(vectors: &Path, seeds: &Path) -> Result<Vec<(String, SapientMessage)>> {
    let mut paths: Vec<_> = fs::read_dir(vectors)?
        .map(|entry| entry.map(|e| e.path()))
        .collect::<std::io::Result<_>>()?;
    paths.sort();
    let mut messages = Vec::new();
    for path in paths
        .iter()
        .filter(|p| p.extension().is_some_and(|e| e == "json"))
    {
        let stem = path
            .file_stem()
            .and_then(|s| s.to_str())
            .ok_or("vector name is not UTF-8")?;
        let msg = json::decode(&fs::read_to_string(path)?)
            .map_err(|e| format!("{}: {e}", path.display()))?;
        write(
            &seeds.join("sapient_message"),
            &format!("{}.bin", snake_case(stem)),
            &msg.encode_to_vec(),
        )?;
        messages.push((stem.to_string(), msg));
    }
    Ok(messages)
}

/// `tcp_frame`: each framing mode, with the payloads its feeds carry.
fn tcp_frame(dir: &Path, sapient: [&SapientMessage; 4]) -> Result<()> {
    let cot = concat!(
        r#"<event version="2.0" uid="sensor-1" type="a-u-A" how="m-g" time="2026-01-01T00:00:00Z" start="2026-01-01T00:00:00Z" stale="2026-01-01T00:01:00Z"><point lat="0.5" lon="0.5" hae="100" ce="10" le="10"/></event>"#,
        "\r\n",
        r#"<event version="2.0" uid="sensor-2" type="a-u-G" how="m-g" time="2026-01-01T00:00:01Z" start="2026-01-01T00:00:01Z" stale="2026-01-01T00:01:01Z"><point lat="0.6" lon="0.6" hae="0" ce="10" le="10"/></event>"#,
        "\n",
    );
    write(
        dir,
        "newline_cot.bin",
        &with_selector(tcp_selector(Framing::Newline, 7), cot.as_bytes()),
    )?;
    let ndjson =
        "{\"source\":\"192.0.2.10\",\"track\":1}\n{\"source\":\"198.51.100.20\",\"track\":2}\r\n\n";
    write(
        dir,
        "newline_json.bin",
        &with_selector(tcp_selector(Framing::Newline, 1), ndjson.as_bytes()),
    )?;
    let [registration, status, detection, alert] = sapient;
    for (framing, name, units, read_size) in [
        (
            Framing::Len32Be,
            "len32be_sapient.bin",
            [registration, status],
            13,
        ),
        (
            Framing::Len32Le,
            "len32le_sapient.bin",
            [detection, alert],
            64,
        ),
    ] {
        let mut stream = Vec::new();
        for unit in units {
            stream.extend(framing.encode(&unit.encode_to_vec())?);
        }
        write(
            dir,
            name,
            &with_selector(tcp_selector(framing, read_size), &stream),
        )?;
    }
    write(
        dir,
        "len32le_oversize.bin",
        &with_selector(
            tcp_selector(Framing::Len32Le, 1),
            &[0xff, 0xff, 0xff, 0x7f, b'x'],
        ),
    )?;
    write(
        dir,
        "stream_capture.bin",
        &with_selector(
            tcp_selector(Framing::Stream, 2),
            b"\x00\x01raw capture\xff\xfe",
        ),
    )
}

/// `sapient_frame`: detected and pinned byte orders, and a prefix only the decoder settles.
fn sapient_frame(dir: &Path, first: &SapientMessage, second: &SapientMessage) -> Result<()> {
    for (pinned, order, name, read_size) in [
        (None, ByteOrder::Le, "detect_le.bin", 1),
        (None, ByteOrder::Be, "detect_be.bin", 9),
        (Some(ByteOrder::Le), ByteOrder::Le, "pinned_le.bin", 64),
        (Some(ByteOrder::Be), ByteOrder::Be, "pinned_be.bin", 3),
    ] {
        let mut stream = wire::encode(first, order);
        stream.extend(wire::encode(second, order));
        write(
            dir,
            name,
            &with_selector(sapient_selector(pinned, read_size), &stream),
        )?;
    }
    // [0, 0, 1, 0] reads 256 big-endian and 65,536 little-endian. Both are possible
    // lengths, so the arm settles the order by which payload decodes.
    let mut stream = wire::encode(&message_of_length(256)?, ByteOrder::Be);
    if stream.get(..4) != Some(&[0, 0, 1, 0][..]) {
        return Err("the tie seed does not start with [0, 0, 1, 0]".into());
    }
    stream.extend(wire::encode(second, ByteOrder::Be));
    write(
        dir,
        "detect_tie_be.bin",
        &with_selector(sapient_selector(None, 32), &stream),
    )
}

/// `downlink_gate`: declared entries (`+`), then the pen keys samples arrive on.
fn downlink_gate(dir: &Path) -> Result<()> {
    let gate = concat!(
        "+aa11/sapient/radar1\n",
        "+bb22/sensors/**\n",
        "+release/lane-a/**\n",
        "+**\n",
        "+release/**\n",
        "+ops/status\n",
        "+@/admin\n",
        "+downlink/aa11\n",
        "downlink/aa11/sapient/radar1\n",
        "downlink/aa11/sapient/radar2\n",
        "downlink/bb22/sensors/radar3/data\n",
        "downlink/release/lane-a/aa11/tracks\n",
        "downlink/release/lane-b/aa11/tracks\n",
        "downlink/cc33/sapient/radar1\n",
        "raw/octo/sapient/192-0-2-10\n",
        "downlink/\n",
    );
    write(dir, "allow_and_samples.txt", gate.as_bytes())
}

fn main() -> Result<()> {
    let root = Path::new(env!("CARGO_MANIFEST_DIR"));
    let seeds = root.join("seeds");
    // Start each target's directory empty, so a seed that is no longer written goes away.
    // seeds/NOTICE is not written here and stays.
    for target in TARGETS {
        let dir = seeds.join(target);
        if dir.exists() {
            fs::remove_dir_all(&dir)?;
        }
    }
    let messages =
        reference_messages(&root.join("../sapient-receiver/tests/vectors/true"), &seeds)?;
    let find = |stem: &str| {
        messages
            .iter()
            .find(|(s, _)| s == stem)
            .map(|(_, m)| m)
            .ok_or_else(|| format!("reference vector {stem} is missing"))
    };
    let registration = find("Registration.FollowObject")?;
    let status = find("StatusReport.FullMessage")?;
    let detection = find("DetectionReport.FullMessage")?;
    let alert = find("Alert.FullMessage")?;

    tcp_frame(
        &seeds.join("tcp_frame"),
        [registration, status, detection, alert],
    )?;
    sapient_frame(&seeds.join("sapient_frame"), registration, status)?;
    downlink_gate(&seeds.join("downlink_gate"))?;
    println!(
        "wrote seeds for {} reference vectors to {}",
        messages.len(),
        seeds.display()
    );
    Ok(())
}
