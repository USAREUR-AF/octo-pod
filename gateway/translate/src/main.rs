// SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
// SPDX-License-Identifier: Apache-2.0
//! octo-translate: convert captured pen samples from one format to others.
//!
//! One process per declared gateway, as one arm process per publisher: its own pod-CA
//! certificate, its own namespace, its own audit record. See the crate documentation
//! (`src/lib.rs`) for what it reads, what it writes and how it fails, and
//! `docs/adr/0004-host-the-translation-gateway-beside-the-arms.md` for why it is not an
//! arm.

use std::process::ExitCode;
use std::time::Duration;

use anyhow::Context;
use clap::Parser;
use octo_arm_common::SessionConfig;
use octo_translate::gateway::{
    gateway, Declaration, DEFAULT_MAX_AGE, DEFAULT_MAX_BYTES, DEFAULT_QUEUE, REFUSED,
};
use octo_translate::route::DEFAULT_TEMPLATE;

#[derive(Parser, Debug)]
#[command(
    name = "octo-translate",
    about = "Convert captured pen samples from one declared format to others, under this gateway's own identity"
)]
struct Args {
    /// This gateway's id; names the audit file and appears in every record.
    #[arg(long, env = "TRANSLATE_ID")]
    id: String,
    /// Pen key expressions to read captured samples from, comma-separated.
    #[arg(long, env = "TRANSLATE_SOURCES", value_delimiter = ',')]
    sources: Vec<String>,
    /// The format of every sample on those keys: a pack name, for example `sapient`.
    #[arg(long, env = "TRANSLATE_FROM")]
    from: String,
    /// The formats to convert each sample to, comma-separated pack names.
    #[arg(long, env = "TRANSLATE_TO", value_delimiter = ',')]
    to: Vec<String>,
    /// The literal key every result is published beneath, for example `raw/xl1`.
    #[arg(long, env = "TRANSLATE_NAMESPACE")]
    namespace: String,
    /// The publish key template. Placeholders: {namespace} {from} {to} {source} {tail}.
    #[arg(long, env = "TRANSLATE_PUBLISH_KEY", default_value = DEFAULT_TEMPLATE)]
    publish_key: String,
    /// Model keys the source format does not carry, as `key=value`, semicolon-separated.
    /// A value never replaces one the source message carries.
    #[arg(long = "set", env = "TRANSLATE_CONTEXT", value_delimiter = ';')]
    context: Vec<String>,
    /// Convert a sample only when each of these decoded model keys has this value, as
    /// `key=value`, semicolon-separated. Any other sample is skipped and counted.
    #[arg(long = "only-when", env = "TRANSLATE_ONLY_WHEN", value_delimiter = ';')]
    only_when: Vec<String>,
    /// Give each result a stale time this many seconds after the sample was received,
    /// when the source carries none. 0 adds none.
    #[arg(long, env = "TRANSLATE_STALE_AFTER_S", default_value_t = 0)]
    stale_after_s: u64,
    /// Give each result the time the sample was received, when the source carries none.
    #[arg(long, env = "TRANSLATE_STAMP_TIME", default_value_t = false, action = clap::ArgAction::Set)]
    stamp_time: bool,
    /// The pinned pack bundle: MANIFEST.json, model/ and packs/.
    #[arg(
        long,
        env = "TRANSLATE_PACKS_DIR",
        default_value = "/usr/share/octo-translate/packs"
    )]
    packs_dir: String,
    /// `public` or `controlled`: the most restricted pack the engine may load.
    #[arg(long, env = "TRANSLATE_MAX_DISTRIBUTION", default_value = "public")]
    max_distribution: String,
    /// The pen router endpoint, e.g. tls/127.0.0.1:7447.
    #[arg(long, env = "TRANSLATE_ENDPOINT", default_value = "tls/127.0.0.1:7447")]
    endpoint: String,
    /// This gateway's pod-CA certificate (PEM).
    #[arg(long, env = "TRANSLATE_CERT")]
    cert: Option<String>,
    /// Its private key (PEM).
    #[arg(long, env = "TRANSLATE_KEY")]
    key: Option<String>,
    /// The pod CA (PEM).
    #[arg(long, env = "TRANSLATE_ROOT_CA")]
    root_ca: Option<String>,
    /// Where the audit record is kept.
    #[arg(
        long,
        env = "TRANSLATE_AUDIT_DIR",
        default_value = "/var/lib/octo-translate"
    )]
    audit_dir: String,
    /// Seconds between stats lines and audit summaries.
    #[arg(long, env = "TRANSLATE_STATS_SECS", default_value_t = 60)]
    stats_secs: u64,
    /// Queue length, in samples. A full queue drops the newest sample and counts it.
    #[arg(long, env = "TRANSLATE_QUEUE", default_value_t = DEFAULT_QUEUE)]
    queue: usize,
    /// Largest sample accepted, in bytes. A larger one is dropped and counted.
    #[arg(long, env = "TRANSLATE_MAX_BYTES", default_value_t = DEFAULT_MAX_BYTES)]
    max_bytes: usize,
    /// Milliseconds a sample may wait in the queue before it is dropped, not converted.
    #[arg(long, env = "TRANSLATE_MAX_AGE_MS", default_value_t = DEFAULT_MAX_AGE.as_millis() as u64)]
    max_age_ms: u64,
}

/// `key=value` text to pairs. A piece without `=` becomes a pair with an empty key, which
/// the declaration check refuses by name.
fn pairs(context: &[String]) -> Vec<(String, String)> {
    context
        .iter()
        .map(|kv| kv.trim())
        .filter(|kv| !kv.is_empty())
        .map(|kv| match kv.split_once('=') {
            Some((k, v)) => (k.trim().to_string(), v.to_string()),
            None => (String::new(), kv.to_string()),
        })
        .collect()
}

fn declaration(args: Args) -> Declaration {
    let trimmed = |list: Vec<String>| -> Vec<String> {
        list.into_iter()
            .map(|s| s.trim().to_string())
            .filter(|s| !s.is_empty())
            .collect()
    };
    Declaration {
        id: args.id,
        sources: trimmed(args.sources),
        from: args.from,
        to: trimmed(args.to),
        namespace: args.namespace,
        publish_key: args.publish_key,
        context: pairs(&args.context),
        only_when: pairs(&args.only_when),
        stale_after: (args.stale_after_s > 0).then(|| Duration::from_secs(args.stale_after_s)),
        stamp_time: args.stamp_time,
        packs_dir: args.packs_dir,
        max_distribution: args.max_distribution,
        pen: SessionConfig {
            endpoint: args.endpoint,
            cert: args.cert,
            key: args.key,
            root_ca: args.root_ca,
        },
        audit_dir: args.audit_dir,
        stats_secs: args.stats_secs,
        queue: args.queue,
        max_bytes: args.max_bytes,
        max_age: Duration::from_millis(args.max_age_ms),
    }
}

#[tokio::main]
async fn main() -> ExitCode {
    octo_arm_common::init_tracing();
    let code = gateway(declaration(Args::parse()), async {
        tokio::signal::ctrl_c().await.context("waiting for ctrl-c")
    })
    .await;
    debug_assert!(code == 0 || code == 1 || code == REFUSED);
    ExitCode::from(code)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn parse(extra: &[&str]) -> Declaration {
        let mut argv = vec![
            "octo-translate",
            "--id",
            "xl1",
            "--sources",
            "raw/radar1/sapient/*, raw/radar2/sapient/*",
            "--from",
            "sapient",
            "--to",
            "cot-xml,geojson",
            "--namespace",
            "raw/xl1",
        ];
        argv.extend_from_slice(extra);
        declaration(Args::try_parse_from(argv).unwrap())
    }

    #[test]
    fn the_defaults_are_public_packs_a_bounded_queue_and_no_added_values() {
        let d = parse(&[]);
        assert_eq!(d.sources, ["raw/radar1/sapient/*", "raw/radar2/sapient/*"]);
        assert_eq!(d.to, ["cot-xml", "geojson"]);
        assert_eq!(d.max_distribution, "public");
        assert_eq!(d.publish_key, DEFAULT_TEMPLATE);
        assert_eq!((d.queue, d.max_bytes), (DEFAULT_QUEUE, DEFAULT_MAX_BYTES));
        assert_eq!(d.max_age, DEFAULT_MAX_AGE);
        assert!(d.context.is_empty() && d.stale_after.is_none() && !d.stamp_time);
        assert_eq!(d.only_when, Vec::<(String, String)>::new());
        assert_eq!(d.pen.endpoint, "tls/127.0.0.1:7447");
    }

    #[test]
    fn context_is_semicolon_separated_so_a_value_may_hold_a_comma() {
        let d = parse(&["--set", "how=m-g;callsign=A,B", "--stale-after-s", "30"]);
        assert_eq!(
            d.context,
            [
                ("how".to_string(), "m-g".to_string()),
                ("callsign".to_string(), "A,B".to_string())
            ]
        );
        assert_eq!(d.stale_after, Some(Duration::from_secs(30)));
    }

    #[test]
    fn a_context_piece_without_an_equals_sign_keeps_an_empty_key_for_the_check_to_refuse() {
        assert_eq!(
            pairs(&["how".to_string()]),
            [(String::new(), "how".to_string())]
        );
    }
}
