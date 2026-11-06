// SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
// SPDX-License-Identifier: Apache-2.0
//! Reference verdict oracle. Inputs are byte-for-byte upstream fixtures.
use sapient_receiver::{json, validate};
use sha2::{Digest, Sha256};
use std::{fmt::Write, fs, path::Path};

#[test]
fn reference_corpus_231() {
    let root = Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/vectors");
    let manifest: serde_json::Value =
        serde_json::from_str(&fs::read_to_string(root.join("manifest.json")).unwrap()).unwrap();
    let entries = manifest.as_array().unwrap();
    assert_eq!(entries.len(), 231);
    let mut positives = 0;
    let mut negatives = 0;
    let mut decode_rejections = 0;
    let mut validation_rejections = 0;
    let mut failures = Vec::new();
    let mut results = Vec::new();
    let mut paths = std::collections::BTreeSet::new();
    for entry in entries {
        let path = entry["path"].as_str().unwrap();
        assert!(
            paths.insert(path.to_owned()),
            "duplicate manifest path {path}"
        );
        let bytes = fs::read(root.join(path)).unwrap();
        let digest = Sha256::digest(&bytes);
        let mut digest_hex = String::with_capacity(digest.len() * 2);
        for byte in digest.iter() {
            write!(&mut digest_hex, "{byte:02x}").unwrap();
        }
        assert_eq!(
            digest_hex,
            entry["sha256"].as_str().unwrap(),
            "modified reference fixture: {path}"
        );
        let expected = path.starts_with("true/");
        if expected {
            positives += 1;
        } else {
            negatives += 1;
        }
        let (valid, stage, details) = match json::decode(std::str::from_utf8(&bytes).unwrap()) {
            Err(e) => {
                decode_rejections += 1;
                (false, "decode", e.to_string())
            }
            Ok(msg) => {
                let errors = validate::violations(&msg);
                if !errors.is_empty() {
                    validation_rejections += 1;
                }
                (errors.is_empty(), "validate", errors.join("; "))
            }
        };
        println!(
            "{} {path} expected={expected} actual={valid} stage={stage} {details}",
            if valid == expected { "PASS" } else { "FAIL" }
        );
        results.push(serde_json::json!({"path":path,"expected":expected,"actual":valid,"stage":stage,"details":details}));
        if valid != expected {
            failures.push(format!("{path}: {stage}: {details}"));
        }
    }
    let disk: std::collections::BTreeSet<_> = ["true", "false"]
        .iter()
        .flat_map(|category| {
            fs::read_dir(root.join(category))
                .unwrap()
                .map(move |entry| {
                    format!(
                        "{category}/{}",
                        entry.unwrap().file_name().to_str().unwrap()
                    )
                })
        })
        .collect();
    assert_eq!(
        paths, disk,
        "every vendored fixture must be tested exactly once"
    );
    assert_eq!((positives, negatives), (38, 193));
    if let Ok(report) = std::env::var("SAPIENT_CORPUS_REPORT") {
        fs::write(report, serde_json::to_vec_pretty(&serde_json::json!({
            "reference_commit":"6a2999d70d761d224914ddd8fa3f663af5005be0", "total":231,
            "decode_rejections":decode_rejections, "validation_rejections":validation_rejections,
            "mismatches":failures.len(), "results":results
        })).unwrap()).unwrap();
    }
    println!("Corpus: 231; true={positives}; false={negatives}; decode_rejections={decode_rejections}; validation_rejections={validation_rejections}; mismatches={}", failures.len());
    assert!(
        failures.is_empty(),
        "{} mismatches:\n{}",
        failures.len(),
        failures.join("\n")
    );
}
