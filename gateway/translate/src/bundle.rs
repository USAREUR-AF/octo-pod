// SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
// SPDX-License-Identifier: Apache-2.0
//! The pinned pack bundle: model modules and packs copied from goat-transformers at one
//! commit, with a manifest of that commit and every file's SHA-256.
//!
//! The engine and its packs must come from the same commit, and the mappings a pod runs
//! must be the ones that were reviewed. So the bundle is checked before anything is
//! loaded: every file under `model/` and `packs/` must be listed in `MANIFEST.json` with
//! a matching hash, and every listed file must be present. A bundle that fails the check
//! is not loaded and the gateway does not start.
//!
//! `tools/refresh-packs.py` writes the bundle and the manifest, and makes the same check.

use std::collections::BTreeMap;
use std::fmt;
use std::fs;
use std::path::{Path, PathBuf};

use goat_transformers::{Distribution, Engine, Pack, Wire};

/// Why a bundle was not loaded.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BundleError(pub String);

impl fmt::Display for BundleError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for BundleError {}

fn refuse<T>(why: impl Into<String>) -> Result<T, BundleError> {
    Err(BundleError(why.into()))
}

/// A loaded bundle: the engine, and what the gateway needs to know about each format
/// that the engine does not report.
pub struct Bundle {
    /// The engine with every model module and pack of the bundle loaded.
    pub engine: Engine,
    /// The goat-transformers commit the bundle was copied from.
    pub commit: String,
    /// Each loaded format's wire type.
    wires: BTreeMap<String, Wire>,
}

impl fmt::Debug for Bundle {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("Bundle")
            .field("commit", &self.commit)
            .field("formats", &self.formats())
            .finish_non_exhaustive()
    }
}

/// `sha256:<hex>`-less hex digest of a file's bytes, as the manifest holds it.
fn sha256_hex(bytes: &[u8]) -> String {
    hex::encode(ring::digest::digest(&ring::digest::SHA256, bytes).as_ref())
}

/// Every file under `dir`, as a path relative to `root` with `/` separators.
fn files_under(root: &Path, dir: &Path, out: &mut Vec<String>) -> Result<(), BundleError> {
    let entries = match fs::read_dir(dir) {
        Ok(entries) => entries,
        Err(e) => return refuse(format!("cannot read {}: {e}", dir.display())),
    };
    for entry in entries {
        let path = entry
            .map_err(|e| BundleError(format!("cannot read {}: {e}", dir.display())))?
            .path();
        if path.is_dir() {
            files_under(root, &path, out)?;
        } else if let Ok(rel) = path.strip_prefix(root) {
            let parts: Vec<String> = rel
                .components()
                .map(|c| c.as_os_str().to_string_lossy().into_owned())
                .collect();
            out.push(parts.join("/"));
        }
    }
    Ok(())
}

/// Check `dir` against its manifest. Returns the commit and the verified files.
fn verify(dir: &Path) -> Result<(String, BTreeMap<String, Vec<u8>>), BundleError> {
    let manifest_path = dir.join("MANIFEST.json");
    let text = fs::read_to_string(&manifest_path)
        .map_err(|e| BundleError(format!("cannot read {}: {e}", manifest_path.display())))?;
    let manifest: serde_json::Value = serde_json::from_str(&text)
        .map_err(|e| BundleError(format!("{} is not JSON: {e}", manifest_path.display())))?;
    let Some(commit) = manifest["commit"].as_str().filter(|c| !c.is_empty()) else {
        return refuse("the manifest names no commit");
    };
    let Some(entries) = manifest["files"].as_array() else {
        return refuse("the manifest lists no files");
    };

    let mut listed: BTreeMap<String, String> = BTreeMap::new();
    for entry in entries {
        match (entry["path"].as_str(), entry["sha256"].as_str()) {
            (Some(path), Some(hash)) if !path.contains("..") && !path.starts_with('/') => {
                listed.insert(path.to_string(), hash.to_string());
            }
            _ => return refuse(format!("the manifest has an entry it cannot use: {entry}")),
        }
    }

    let mut present = Vec::new();
    for sub in ["model", "packs"] {
        let sub_dir = dir.join(sub);
        if sub_dir.is_dir() {
            files_under(dir, &sub_dir, &mut present)?;
        }
    }
    if let Some(extra) = present.iter().find(|p| !listed.contains_key(*p)) {
        return refuse(format!("{extra} is in the bundle and not in its manifest"));
    }

    let mut verified = BTreeMap::new();
    for (path, hash) in &listed {
        let full: PathBuf = dir.join(path);
        let bytes = fs::read(&full)
            .map_err(|e| BundleError(format!("{path} is in the manifest and unreadable: {e}")))?;
        if sha256_hex(&bytes) != *hash {
            return refuse(format!("{path} differs from the manifest"));
        }
        verified.insert(path.clone(), bytes);
    }
    Ok((commit.to_string(), verified))
}

impl Bundle {
    /// Verify the bundle in `dir`, then load it into an engine that refuses anything
    /// above `max_distribution`.
    ///
    /// # Errors
    ///
    /// Fails when the bundle does not match its manifest, when a pack or module is above
    /// `max_distribution` (the engine's `distribution-refused`), or when the engine
    /// refuses a file for any other reason. The engine's error is in the message.
    pub fn load(dir: &Path, max_distribution: Distribution) -> Result<Self, BundleError> {
        let (commit, verified) = verify(dir)?;
        let mut engine = Engine::new();
        engine.set_max_distribution(max_distribution);
        // The engine's directory loader orders modules (core first, then each module
        // after the modules it uses). The files it reads are the ones just verified.
        engine
            .load_model_dir(&dir.join("model"))
            .map_err(|e| BundleError(format!("model modules refused: {e}")))?;

        let mut wires = BTreeMap::new();
        for (path, bytes) in &verified {
            if !(path.starts_with("packs/") && path.ends_with("/pack.def")) {
                continue;
            }
            let text = std::str::from_utf8(bytes)
                .map_err(|_| BundleError(format!("{path} is not UTF-8")))?;
            let pack =
                Pack::parse(text).map_err(|e| BundleError(format!("{path} refused: {e}")))?;
            let (format, wire) = (pack.format.clone(), pack.wire);
            engine
                .load(pack)
                .map_err(|e| BundleError(format!("{path} refused: {e}")))?;
            wires.insert(format, wire);
        }
        if wires.is_empty() {
            return refuse("the bundle holds no packs");
        }
        Ok(Self {
            engine,
            commit,
            wires,
        })
    }

    /// The loaded format names, sorted.
    #[must_use]
    pub fn formats(&self) -> Vec<String> {
        self.wires.keys().cloned().collect()
    }

    /// A loaded format's wire type.
    #[must_use]
    pub fn wire(&self, format: &str) -> Option<Wire> {
        self.wires.get(format).copied()
    }
}

/// Parse `public` or `controlled`.
///
/// # Errors
///
/// Fails on any other value.
pub fn distribution(text: &str) -> Result<Distribution, BundleError> {
    Distribution::parse(text)
        .ok_or_else(|| BundleError(format!("{text:?} is not `public` or `controlled`")))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn shipped() -> PathBuf {
        Path::new(env!("CARGO_MANIFEST_DIR")).join("packs")
    }

    /// A private copy of the shipped bundle a test may damage.
    fn copy(name: &str) -> PathBuf {
        let to = std::env::temp_dir().join(format!("octo-translate-{}-{name}", std::process::id()));
        let _ = fs::remove_dir_all(&to);
        let mut files = Vec::new();
        files_under(&shipped(), &shipped(), &mut files).unwrap();
        for rel in files {
            let target = to.join(&rel);
            fs::create_dir_all(target.parent().unwrap()).unwrap();
            fs::copy(shipped().join(&rel), target).unwrap();
        }
        to
    }

    #[test]
    fn the_shipped_bundle_matches_its_manifest_and_loads_public_only() {
        let bundle = Bundle::load(&shipped(), Distribution::Public).unwrap();
        assert_eq!(bundle.formats(), ["cot-xml", "geojson", "sapient"]);
        assert_eq!(bundle.wire("sapient"), Some(Wire::Protobuf));
        assert_eq!(bundle.wire("cot-xml"), Some(Wire::Xml));
        assert_eq!(bundle.wire("geojson"), Some(Wire::GeoJson));
        assert_eq!(bundle.commit.len(), 40);
    }

    #[test]
    fn the_manifest_commit_is_the_rev_the_crate_pins() {
        let cargo =
            fs::read_to_string(Path::new(env!("CARGO_MANIFEST_DIR")).join("Cargo.toml")).unwrap();
        let bundle = Bundle::load(&shipped(), Distribution::Public).unwrap();
        assert!(
            cargo.contains(&format!("rev = \"{}\"", bundle.commit)),
            "the engine and the packs must be one commit"
        );
    }

    #[test]
    fn a_changed_pack_is_refused() {
        let dir = copy("changed");
        let pack = dir.join("packs/cot-xml/pack.def");
        let mut text = fs::read_to_string(&pack).unwrap();
        text.push_str("field   callsign  detail/@other  string\n");
        fs::write(&pack, text).unwrap();
        let why = Bundle::load(&dir, Distribution::Public).unwrap_err();
        assert_eq!(why.0, "packs/cot-xml/pack.def differs from the manifest");
        fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn a_file_the_manifest_does_not_list_is_refused() {
        let dir = copy("extra");
        fs::create_dir_all(dir.join("packs/extra")).unwrap();
        fs::write(dir.join("packs/extra/pack.def"), "format extra\n").unwrap();
        let why = Bundle::load(&dir, Distribution::Public).unwrap_err();
        assert!(why.0.contains("not in its manifest"), "{why}");
        fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn a_listed_file_that_is_missing_is_refused() {
        let dir = copy("missing");
        fs::remove_file(dir.join("model/track.def")).unwrap();
        let why = Bundle::load(&dir, Distribution::Public).unwrap_err();
        assert!(why.0.contains("model/track.def"), "{why}");
        fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn a_bundle_without_a_manifest_is_refused() {
        let dir = copy("nomanifest");
        fs::remove_file(dir.join("MANIFEST.json")).unwrap();
        assert!(Bundle::load(&dir, Distribution::Public).is_err());
        fs::remove_dir_all(dir).unwrap();
    }

    /// A controlled pack with a correct manifest entry still does not load in an engine
    /// limited to public: the engine's own check, reached through this loader.
    #[test]
    fn a_controlled_pack_is_refused_by_a_public_engine_and_loads_in_a_controlled_one() {
        let dir = copy("controlled");
        let pack = dir.join("packs/geojson/pack.def");
        let text = fs::read_to_string(&pack)
            .unwrap()
            .replace("distribution public", "distribution controlled");
        fs::write(&pack, &text).unwrap();
        let manifest_path = dir.join("MANIFEST.json");
        let mut manifest: serde_json::Value =
            serde_json::from_str(&fs::read_to_string(&manifest_path).unwrap()).unwrap();
        for entry in manifest["files"].as_array_mut().unwrap() {
            if entry["path"] == "packs/geojson/pack.def" {
                entry["sha256"] = sha256_hex(text.as_bytes()).into();
            }
        }
        fs::write(&manifest_path, manifest.to_string()).unwrap();

        let why = Bundle::load(&dir, Distribution::Public).unwrap_err();
        assert!(why.0.contains("distribution-refused"), "{why}");
        assert!(Bundle::load(&dir, Distribution::Controlled).is_ok());
        fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn the_distribution_limit_is_one_of_two_words() {
        assert_eq!(distribution("public").unwrap(), Distribution::Public);
        assert_eq!(
            distribution("controlled").unwrap(),
            Distribution::Controlled
        );
        assert!(distribution("secret").is_err());
    }
}
