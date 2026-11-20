// SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
// SPDX-License-Identifier: Apache-2.0
//! One conversion, and why a sample was not converted.
//!
//! The steps are the engine's own `convert`, in its order (engine-spec, "Order inside
//! convert"): decode, clear the kind, fill the declared context, apply the derivations,
//! encode. They are called one at a time here for one reason: between decode and fill,
//! the gateway counts how many values of the source message the target format has a
//! place for. With none, the engine would still encode a message made only of the
//! target's defaults and the declared context, which says nothing the source said. That
//! sample is skipped, not published.
//!
//! Two declared rules are applied at the same point, on the decoded model, by model key
//! name. Neither is a mapping: this crate still holds no field name of any format.
//!
//! - `only_when key=value`: a sample whose decoded `key` is absent or has another value
//!   is skipped (`condition-not-met`). It is how a declaration says "convert only the
//!   samples these packs read correctly", for example one coordinate system.
//! - `carry target=source`: when the sample carries no `target` and does carry `source`,
//!   `target` takes the value of `source`. It is how a declaration gives a result an
//!   identifier the source message has under a key the target format does not read.
//!
//! Binary wire types (`protobuf`, `bits`) pass through the engine as hexadecimal text;
//! the pen carries the bytes. The conversion to and from hex is here and nowhere else.

use std::fmt;

use goat_transformers::{Engine, Grade, Value, Wire};

use crate::bundle::Bundle;

/// Why a sample produced no output.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Outcome {
    /// The engine refused the sample: it does not parse as the source format, or the
    /// target format cannot be written from it.
    Invalid,
    /// The sample parsed, and the target format has no place for anything it carries.
    Skipped,
}

/// A sample that was not converted.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Failure {
    /// Counted as invalid or as skipped.
    pub outcome: Outcome,
    /// The engine's error name (`no-kind`, `malformed-wire`, ...), `unnamed` when the
    /// engine's message carries none, or one of this gateway's own: `no-shared-value`,
    /// `condition-not-met`, `not-text`, `bad-publish-key`, `engine-panic`.
    pub name: String,
    /// The engine's message, complete.
    pub detail: String,
}

impl fmt::Display for Failure {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}: {}", self.name, self.detail)
    }
}

/// The name at the start of an engine message: `no-kind: ...` gives `no-kind`. The
/// engine names its specified errors this way; a message without a name gives `unnamed`.
#[must_use]
pub fn error_name(message: &str) -> &str {
    match message.split_once(':') {
        Some((head, _))
            if !head.is_empty()
                && head.starts_with(|c: char| c.is_ascii_lowercase())
                && head
                    .chars()
                    .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == '-') =>
        {
            head
        }
        _ => "unnamed",
    }
}

fn invalid(stage: &str, e: &goat_transformers::Error) -> Failure {
    Failure {
        outcome: Outcome::Invalid,
        name: error_name(&e.msg).to_string(),
        detail: format!("{stage}: {}", e.msg),
    }
}

/// Whether a wire type passes through the engine as hexadecimal text.
#[must_use]
pub fn is_binary(wire: Wire) -> bool {
    matches!(wire, Wire::Protobuf | Wire::Bits | Wire::Tlv)
}

/// A declared conversion the gateway refuses to run.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PlanError(pub String);

impl fmt::Display for PlanError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for PlanError {}

/// One source format, the target formats, and the declared context, checked against the
/// loaded bundle.
pub struct Translator {
    bundle: Bundle,
    from: String,
    from_wire: Wire,
    targets: Vec<(String, Wire)>,
    context: Vec<(String, Value)>,
    /// (key, required value): see the module text.
    only_when: Vec<(String, Value)>,
    /// (target key, source key): see the module text.
    carry: Vec<(String, String)>,
}

impl fmt::Debug for Translator {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("Translator")
            .field("from", &self.from)
            .field("targets", &self.targets())
            .field("bundle", &self.bundle)
            .finish_non_exhaustive()
    }
}

impl Translator {
    /// Check a declared conversion against the bundle.
    ///
    /// # Errors
    ///
    /// Fails when a format is not in the bundle, a target is the source format, a target
    /// is named twice, a context key is not declared by a loaded model module or its
    /// value is not of the key's type, or a target shares no model key with the source
    /// (the engine grades that pair `None`: no conversion between them carries anything).
    pub fn new(
        bundle: Bundle,
        from: &str,
        to: &[String],
        context: &[(String, String)],
    ) -> Result<Self, PlanError> {
        let known = bundle.formats().join(", ");
        let Some(from_wire) = bundle.wire(from) else {
            return Err(PlanError(format!(
                "source format {from:?} is not in the pack bundle (it holds: {known})"
            )));
        };
        if to.is_empty() {
            return Err(PlanError("no target format declared".into()));
        }
        let mut targets: Vec<(String, Wire)> = Vec::new();
        for target in to {
            let Some(wire) = bundle.wire(target) else {
                return Err(PlanError(format!(
                    "target format {target:?} is not in the pack bundle (it holds: {known})"
                )));
            };
            if target == from {
                return Err(PlanError(format!(
                    "target format {target:?} is the source format: nothing to convert"
                )));
            }
            if targets.iter().any(|(t, _)| t == target) {
                return Err(PlanError(format!(
                    "target format {target:?} is declared twice"
                )));
            }
            if bundle.engine.capability(from, target).grade == Grade::None {
                return Err(PlanError(format!(
                    "{from} and {target} share no model key: a conversion would carry nothing"
                )));
            }
            targets.push((target.clone(), wire));
        }
        let context = bundle
            .engine
            .context_from_text(context)
            .map_err(|e| PlanError(format!("declared context refused: {e}")))?;
        Ok(Self {
            bundle,
            from: from.to_string(),
            from_wire,
            targets,
            context,
            only_when: Vec::new(),
            carry: Vec::new(),
        })
    }

    /// Add the declared `only_when` and `carry` rules, checked against the bundle.
    ///
    /// # Errors
    ///
    /// Fails when a key is empty or named twice; an `only_when` key or a `carry` source
    /// is not a key the source format decodes; an `only_when` value is not of its key's
    /// type; a `carry` target is its own source, or is not a key any declared target
    /// encodes.
    pub fn with_rules(
        mut self,
        only_when: &[(String, String)],
        carry: &[(String, String)],
    ) -> Result<Self, PlanError> {
        let source_keys = self.engine().model_keys(&self.from);
        for (key, text) in only_when {
            if !source_keys.contains(key) {
                return Err(PlanError(format!(
                    "only_when key {key:?} is not a model key the {} pack decodes",
                    self.from
                )));
            }
            if self.only_when.iter().any(|(k, _)| k == key) {
                return Err(PlanError(format!("only_when names {key} twice")));
            }
            let mut typed = self
                .engine()
                .context_from_text(&[(key.clone(), text.clone())])
                .map_err(|e| PlanError(format!("only_when {key} refused: {e}")))?;
            if let Some(pair) = typed.pop() {
                self.only_when.push(pair);
            }
        }
        for (target, source) in carry {
            if target == source {
                return Err(PlanError(format!(
                    "carry {target}={source} carries nothing"
                )));
            }
            if !source_keys.contains(source) {
                return Err(PlanError(format!(
                    "carry source {source:?} is not a model key the {} pack decodes",
                    self.from
                )));
            }
            if !self
                .targets
                .iter()
                .any(|(t, _)| self.engine().model_keys(t).contains(target))
            {
                return Err(PlanError(format!(
                    "carry target {target:?} is not a model key any declared target encodes"
                )));
            }
            if self.carry.iter().any(|(t, _)| t == target) {
                return Err(PlanError(format!("carry names target {target} twice")));
            }
            self.carry.push((target.clone(), source.clone()));
        }
        Ok(self)
    }

    /// The source format.
    #[must_use]
    pub fn from(&self) -> &str {
        &self.from
    }

    /// The target formats, in declared order.
    #[must_use]
    pub fn targets(&self) -> Vec<&str> {
        self.targets.iter().map(|(t, _)| t.as_str()).collect()
    }

    /// A target format's wire type.
    #[must_use]
    pub fn target_wire(&self, target: &str) -> Option<Wire> {
        self.targets
            .iter()
            .find(|(t, _)| t == target)
            .map(|(_, w)| *w)
    }

    /// The goat-transformers commit the packs came from.
    #[must_use]
    pub fn packs_commit(&self) -> &str {
        &self.bundle.commit
    }

    /// Whether a model key may be supplied as context with this value.
    ///
    /// # Errors
    ///
    /// Fails with the engine's error when no loaded module declares the key or the value
    /// is not of the key's type.
    pub fn accepts_context(&self, key: &str, value: &str) -> Result<(), PlanError> {
        self.engine()
            .context_from_text(&[(key.to_string(), value.to_string())])
            .map(|_| ())
            .map_err(|e| PlanError(format!("context key {key} refused: {e}")))
    }

    fn engine(&self) -> &Engine {
        &self.bundle.engine
    }

    /// Convert one captured sample to one declared target. `extra` is context this
    /// sample adds to the declared context (a value only the host knows, such as when
    /// the sample was received); the declared context is applied first, and neither
    /// replaces a value the source message carries.
    ///
    /// # Errors
    ///
    /// Returns the [`Failure`] to count and record. Nothing is returned in place of a
    /// result: the caller has no bytes to publish.
    ///
    /// The engine reads bytes a sensor chose. If it panics on a sample, that is one
    /// sample not converted (`engine-panic`), not a gateway that restarts on every such
    /// sample: the engine holds no state between conversions for a panic to damage.
    pub fn translate(
        &self,
        target: &str,
        payload: &[u8],
        extra: &[(String, Value)],
    ) -> Result<Vec<u8>, Failure> {
        std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
            self.translate_unguarded(target, payload, extra)
        }))
        .unwrap_or_else(|_| {
            Err(Failure {
                outcome: Outcome::Invalid,
                name: "engine-panic".into(),
                detail: "the engine panicked on this sample".into(),
            })
        })
    }

    fn translate_unguarded(
        &self,
        target: &str,
        payload: &[u8],
        extra: &[(String, Value)],
    ) -> Result<Vec<u8>, Failure> {
        let Some(target_wire) = self.target_wire(target) else {
            return Err(Failure {
                outcome: Outcome::Invalid,
                name: "undeclared-target".into(),
                detail: format!("{target} is not a declared target"),
            });
        };
        let input = if is_binary(self.from_wire) {
            hex::encode(payload)
        } else {
            match std::str::from_utf8(payload) {
                Ok(text) => text.trim().to_string(),
                Err(e) => {
                    return Err(Failure {
                        outcome: Outcome::Invalid,
                        name: "not-text".into(),
                        detail: format!("a {} sample must be UTF-8: {e}", self.from),
                    })
                }
            }
        };

        let engine = self.engine();
        let mut entity = engine
            .decode(&self.from, &input)
            .map_err(|e| invalid("decode", &e))?;
        entity.kind = None;

        // What the source message says that the target can hold. Counted before the
        // context is filled, so a value the gateway supplied is never mistaken for one.
        let target_keys = engine.model_keys(target);
        let carried = entity
            .fields
            .keys()
            .filter(|k| target_keys.contains(*k))
            .count()
            + entity
                .lists
                .keys()
                .filter(|k| target_keys.contains(*k))
                .count();
        if carried == 0 {
            return Err(Failure {
                outcome: Outcome::Skipped,
                name: "no-shared-value".into(),
                detail: format!(
                    "the {} message carries no value that {target} has a field for",
                    self.from
                ),
            });
        }

        for (key, wanted) in &self.only_when {
            if entity.fields.get(key) != Some(wanted) {
                return Err(Failure {
                    outcome: Outcome::Skipped,
                    name: "condition-not-met".into(),
                    detail: format!(
                        "only_when {key}={}: the message has {}",
                        wanted.as_display(),
                        entity
                            .fields
                            .get(key)
                            .map_or_else(|| "no value".to_string(), Value::as_display)
                    ),
                });
            }
        }
        // Before the context, so a value the source message carries wins over a
        // declared one. The value is read again as the target key's type: a carry
        // between keys of different types fails by the engine's own name.
        for (target_key, source_key) in &self.carry {
            if entity.fields.contains_key(target_key) {
                continue;
            }
            if let Some(value) = entity.fields.get(source_key) {
                let typed = engine
                    .context_from_text(&[(target_key.clone(), value.as_display())])
                    .map_err(|e| invalid("carry", &e))?;
                engine
                    .fill(&mut entity, &typed)
                    .map_err(|e| invalid("carry", &e))?;
            }
        }

        engine
            .fill(&mut entity, &self.context)
            .and_then(|()| engine.fill(&mut entity, extra))
            .map_err(|e| invalid("context", &e))?;
        engine.complete(&mut entity);
        let output = engine
            .encode(target, &entity)
            .map_err(|e| invalid("encode", &e))?;

        if is_binary(target_wire) {
            hex::decode(output.trim()).map_err(|e| Failure {
                outcome: Outcome::Invalid,
                name: "unnamed".into(),
                detail: format!("encode: the engine returned text that is not hex: {e}"),
            })
        } else {
            Ok(output.into_bytes())
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use goat_transformers::Distribution;
    use std::path::Path;

    const COT: &str = r#"<event version="2.0" uid="J-01334" type="a-f-G-U-C" time="2021-02-27T20:00:00.000Z" start="2021-02-27T20:00:00.000Z" stale="2021-02-27T20:02:00.000Z" how="m-g"><point lat="30.0090027" lon="-85.9578182" hae="-42.6" ce="45.3" le="99.5"/><detail><contact callsign="GATOR"/></detail></event>"#;
    /// A SAPIENT `DetectionReport` holding only a location (the library's own vector
    /// `packs/sapient/vectors/01-sapient-to-geojson.json`).
    const SAPIENT_HEX: &str =
        "3a21321f09cdcccccccc7c55c011fca9f1d24d023e4019000000000000294038014001";

    fn coordinates(feature: &serde_json::Value) -> Vec<f64> {
        feature["geometry"]["coordinates"]
            .as_array()
            .unwrap()
            .iter()
            .map(|v| v.as_f64().unwrap())
            .collect()
    }

    fn close(got: &[f64], expected: &[f64]) -> bool {
        got.len() == expected.len() && got.iter().zip(expected).all(|(a, b)| (a - b).abs() < 1e-9)
    }

    fn bundle() -> Bundle {
        Bundle::load(
            &Path::new(env!("CARGO_MANIFEST_DIR")).join("packs"),
            Distribution::Public,
        )
        .unwrap()
    }

    fn translator(from: &str, to: &[&str], context: &[(&str, &str)]) -> Translator {
        let to: Vec<String> = to.iter().map(ToString::to_string).collect();
        let context: Vec<(String, String)> = context
            .iter()
            .map(|(k, v)| ((*k).to_string(), (*v).to_string()))
            .collect();
        Translator::new(bundle(), from, &to, &context).unwrap()
    }

    fn plan_error(from: &str, to: &[&str], context: &[(&str, &str)]) -> String {
        let to: Vec<String> = to.iter().map(ToString::to_string).collect();
        let context: Vec<(String, String)> = context
            .iter()
            .map(|(k, v)| ((*k).to_string(), (*v).to_string()))
            .collect();
        Translator::new(bundle(), from, &to, &context)
            .unwrap_err()
            .0
    }

    #[test]
    fn cot_xml_becomes_geojson_with_every_shared_value() {
        let t = translator("cot-xml", &["geojson"], &[]);
        let out = t.translate("geojson", COT.as_bytes(), &[]).unwrap();
        let json: serde_json::Value = serde_json::from_slice(&out).unwrap();
        assert_eq!(json["type"], "Feature");
        assert_eq!(json["id"], "J-01334");
        assert!(
            close(&coordinates(&json), &[-85.957_818_2, 30.009_002_7, -42.6]),
            "{json}"
        );
        assert_eq!(json["properties"]["callsign"], "GATOR");
        assert_eq!(json["properties"]["type"], "a-f-G-U-C");
        assert_eq!(json["properties"]["time_ms"], 1_614_456_000_000_i64);
    }

    #[test]
    fn a_sapient_detection_becomes_cot_xml_and_the_bytes_cross_as_hex() {
        let t = translator("sapient", &["cot-xml"], &[("how", "m-g")]);
        let out = t
            .translate("cot-xml", &hex::decode(SAPIENT_HEX).unwrap(), &[])
            .unwrap();
        let xml = String::from_utf8(out).unwrap();
        assert!(xml.starts_with("<event version=\"2.0\""), "{xml}");
        assert!(
            xml.contains(r#"<point lat="30.009" lon="-85.95" hae="12.5""#),
            "{xml}"
        );
        // Declared context reaches the output; it is a value the source does not carry.
        assert!(xml.contains(r#"how="m-g""#), "{xml}");
    }

    #[test]
    fn cot_xml_becomes_sapient_bytes_that_convert_back_to_the_same_position() {
        let t = translator("cot-xml", &["sapient"], &[]);
        let out = t.translate("sapient", COT.as_bytes(), &[]).unwrap();
        // Protobuf, not hex text: field 1 (the timestamp, which the library derives from
        // the event time), length-delimited.
        assert_eq!(out[0], 0x0a, "field 1 (timestamp), length-delimited");
        let back = translator("sapient", &["geojson"], &[]);
        let json: serde_json::Value =
            serde_json::from_slice(&back.translate("geojson", &out, &[]).unwrap()).unwrap();
        assert!(
            close(&coordinates(&json), &[-85.957_818_2, 30.009_002_7, -42.6]),
            "{json}"
        );
    }

    #[test]
    fn per_sample_context_fills_only_what_the_declared_context_left_absent() {
        let t = translator("sapient", &["cot-xml"], &[("stale_ms", "1000")]);
        let extra = vec![
            ("stale_ms".to_string(), Value::I64(2000)),
            ("time_ms".to_string(), Value::I64(0)),
        ];
        let xml = String::from_utf8(
            t.translate("cot-xml", &hex::decode(SAPIENT_HEX).unwrap(), &extra)
                .unwrap(),
        )
        .unwrap();
        assert!(xml.contains(r#"stale="1970-01-01T00:00:01.000Z""#), "{xml}");
        assert!(xml.contains(r#"time="1970-01-01T00:00:00.000Z""#), "{xml}");
    }

    #[test]
    fn bytes_that_are_not_the_source_format_fail_with_the_engines_error() {
        let t = translator("cot-xml", &["geojson"], &[]);
        let why = t.translate("geojson", b"<event", &[]).unwrap_err();
        assert_eq!(why.outcome, Outcome::Invalid);
        assert_eq!(
            why.detail,
            "decode: malformed-wire: xml: unexpected end in tag <event>"
        );
        assert_eq!(why.name, "malformed-wire");

        let why = t.translate("geojson", &[0xff, 0xfe], &[]).unwrap_err();
        assert_eq!(why.name, "not-text");

        let t = translator("sapient", &["geojson"], &[]);
        let why = t
            .translate("geojson", &[0x3a, 0x7f, 0x01], &[])
            .unwrap_err();
        assert_eq!(why.outcome, Outcome::Invalid);
        assert!(why.detail.starts_with("decode: "), "{why}");
    }

    /// A SAPIENT message with no position (a bare envelope naming a node) parses, and `CoT`
    /// has no field for anything in it. Without the check the engine would write an
    /// event made only of the target's defaults.
    #[test]
    fn a_message_the_target_has_no_field_for_is_skipped_not_published_as_defaults() {
        let t = translator("sapient", &["cot-xml"], &[("how", "m-g")]);
        // field 2 (node_id) = "n", field 12 (error) { field 3 (error_message) = "e" }
        let envelope = [0x12, 0x01, b'n', 0x62, 0x03, 0x1a, 0x01, b'e'];
        let why = t.translate("cot-xml", &envelope, &[]).unwrap_err();
        assert_eq!(why.outcome, Outcome::Skipped, "{why}");
        assert_eq!(why.name, "no-shared-value");
    }

    /// An XML document that stops after an attribute's `=`. The library once indexed
    /// past its end and panicked; since goat-transformers#12 it returns
    /// `malformed-wire`. Either way the sample is one failure and the next one still
    /// converts.
    #[test]
    fn a_truncated_sample_is_one_failure_and_the_next_still_converts() {
        let t = translator("cot-xml", &["geojson"], &[]);
        let why = t.translate("geojson", b"<event uid=", &[]).unwrap_err();
        assert_eq!(why.outcome, Outcome::Invalid);
        assert_eq!(why.name, "malformed-wire");
        assert!(t.translate("geojson", COT.as_bytes(), &[]).is_ok());
    }

    #[test]
    fn a_rule_the_bundle_cannot_apply_is_refused() {
        let rule = |k: &str, v: &str| vec![(k.to_string(), v.to_string())];
        let why = |only_when: &[(String, String)], carry: &[(String, String)]| {
            translator("sapient", &["cot-xml"], &[])
                .with_rules(only_when, carry)
                .unwrap_err()
                .0
        };
        assert!(
            why(&rule("callsign", "x"), &[]).contains("not a model key the sapient pack decodes")
        );
        assert!(why(&rule("sapient.location_coordinate_system", "utm"), &[])
            .contains("key-type-mismatch"));
        assert!(why(&[], &rule("uid", "callsign")).contains("carry source"));
        assert!(why(
            &[],
            &rule("sapient.node_id", "sapient.detection_report_object_id")
        )
        .contains("not a model key any declared target encodes"));
        assert!(why(&[], &rule("lat", "lat")).contains("carries nothing"));
        let twice = [
            rule("uid", "sapient.node_id"),
            rule("uid", "sapient.destination_id"),
        ]
        .concat();
        assert!(why(&[], &twice).contains("twice"));
    }

    /// What a declaration can give a SAPIENT result made from a `CoT` event: the node id
    /// as declared context and the object id carried from the event's uid. The message
    /// timestamp is the event time, split into seconds and nanoseconds by the library
    /// (sapient 0.2.0, goat-transformers#146). The report id stays absent: nothing in
    /// the packs or the model produces it.
    #[test]
    fn a_cot_event_becomes_a_sapient_detection_with_the_declared_node_and_the_carried_object() {
        use prost::Message;
        use sapient_proto::v2::{sapient_message::Content, SapientMessage};
        let node = "0f6a0b3c-1d2e-4f50-8a9b-0c1d2e3f4a5b";
        let rule = vec![(
            "sapient.detection_report_object_id".to_string(),
            "uid".to_string(),
        )];
        let t = translator("cot-xml", &["sapient"], &[("sapient.node_id", node)])
            .with_rules(&[], &rule)
            .unwrap();
        let bytes = t.translate("sapient", COT.as_bytes(), &[]).unwrap();
        let message = SapientMessage::decode(bytes.as_slice()).unwrap();
        assert_eq!(message.node_id.as_deref(), Some(node));
        // 2021-02-27T20:00:00.000Z
        let timestamp = message
            .timestamp
            .expect("the timestamp derived from the event time");
        assert_eq!(timestamp.seconds, 1_614_456_000);
        assert_eq!(timestamp.nanos, 0);
        let Some(Content::DetectionReport(report)) = message.content else {
            panic!("not a DetectionReport: {message:?}");
        };
        assert_eq!(report.object_id.as_deref(), Some("J-01334"));
        assert!(report.report_id.is_none());
    }

    /// A carried value never replaces one the source message has, and a sample
    /// without the source key is converted without it.
    #[test]
    fn a_carry_fills_only_an_absent_target() {
        let rule = vec![("callsign".to_string(), "uid".to_string())];
        let t = translator("cot-xml", &["geojson"], &[])
            .with_rules(&[], &rule)
            .unwrap();
        let json = |xml: &str| -> serde_json::Value {
            serde_json::from_slice(&t.translate("geojson", xml.as_bytes(), &[]).unwrap()).unwrap()
        };
        assert_eq!(json(COT)["properties"]["callsign"], "GATOR");
        let bare = COT.replace(r#"<contact callsign="GATOR"/>"#, "");
        assert_eq!(json(&bare)["properties"]["callsign"], "J-01334");
    }

    #[test]
    fn a_declaration_the_bundle_cannot_serve_is_refused() {
        assert!(plan_error("link16", &["cot-xml"], &[]).contains("source format \"link16\""));
        assert!(plan_error("cot-xml", &["nvg"], &[]).contains("target format \"nvg\""));
        assert!(plan_error("cot-xml", &["cot-xml"], &[]).contains("is the source format"));
        assert!(plan_error("cot-xml", &["geojson", "geojson"], &[]).contains("declared twice"));
        assert!(plan_error("cot-xml", &[], &[]).contains("no target format"));
    }

    #[test]
    fn a_context_key_no_module_declares_or_of_the_wrong_type_is_refused() {
        let why = plan_error("sapient", &["cot-xml"], &[("nosuchkey", "1")]);
        assert!(why.contains("undeclared-key"), "{why}");
        let why = plan_error("sapient", &["cot-xml"], &[("stale_ms", "soon")]);
        assert!(why.contains("key-type-mismatch"), "{why}");
    }

    #[test]
    fn an_error_name_is_the_kebab_word_before_the_first_colon() {
        assert_eq!(error_name("no-kind: no kind matches"), "no-kind");
        assert_eq!(error_name("malformed-wire: protobuf: x"), "malformed-wire");
        assert_eq!(error_name("unexpected end in tag"), "unnamed");
        assert_eq!(error_name("cannot read /x: denied"), "unnamed");
        assert_eq!(error_name(": x"), "unnamed");
    }
}
