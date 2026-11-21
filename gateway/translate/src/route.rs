// SPDX-FileCopyrightText: 2026 U.S. Federal Government (in countries where recognized)
// SPDX-License-Identifier: Apache-2.0
//! Where the gateway reads and where it writes, and the check that the two never meet.
//!
//! The gateway reads the declared source key expressions and writes only beneath its own
//! namespace. A source that intersects the namespace would let it read what it wrote and
//! convert it again, so that declaration is refused before a session is opened. The pen
//! ACL grants the same two things and nothing else; this check holds whether or not the
//! ACL is right.
//!
//! A result is published on a key made from a template. The default,
//! `{namespace}/{to}/{source}`, keeps the whole source key beneath the target format, so
//! the arm, the feed and the source address the capturing arm stamped into its key stay
//! readable in the key of every result, on the pen and after a bridge has carried it
//! upstream.

use std::fmt;

use goat_transformers::Wire;
use zenoh::bytes::Encoding;
use zenoh::key_expr::OwnedKeyExpr;

/// A declaration the gateway refuses to run with.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RouteError(pub String);

impl fmt::Display for RouteError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for RouteError {}

fn refuse<T>(why: String) -> Result<T, RouteError> {
    Err(RouteError(why))
}

/// The default publish key template.
pub const DEFAULT_TEMPLATE: &str = "{namespace}/{to}/{source}";

/// First chunks a source may not have. `downlink` holds fabric data that was released
/// down to this pod: converting it and publishing the result where the uplink can read it
/// would send it back up under this pod's slot. The others are not sensor data. The
/// playbook's validation and `octo-pod translate` hold the same list.
pub const RESERVED_ROOTS: [&str; 4] = ["ops", "attest", "system", "downlink"];

const PLACEHOLDERS: [&str; 5] = ["{namespace}", "{from}", "{to}", "{source}", "{tail}"];

/// The declared sources, the namespace, and the publish key template.
#[derive(Debug, Clone)]
pub struct Route {
    sources: Vec<OwnedKeyExpr>,
    namespace: String,
    template: String,
}

fn literal(what: &str, key: &str) -> Result<OwnedKeyExpr, RouteError> {
    let ke = OwnedKeyExpr::try_from(key.to_string())
        .map_err(|_| RouteError(format!("{what} {key:?} is not a canonical key expression")))?;
    if key.contains('*') || key.contains('$') {
        return refuse(format!("{what} {key:?} must be literal, not a wildcard"));
    }
    Ok(ke)
}

impl Route {
    /// Validate the declaration.
    ///
    /// # Errors
    ///
    /// Fails when there is no source; a source is not a canonical key expression, is
    /// `**`, or begins with a wildcard, `@` or a reserved root; the namespace is not a literal key; a
    /// source intersects the namespace; or the template does not begin with
    /// `{namespace}/`, names an unknown placeholder, or omits both `{source}` and
    /// `{tail}` or `{to}`.
    pub fn new(sources: &[String], namespace: &str, template: &str) -> Result<Self, RouteError> {
        let namespace_ke = literal("namespace", namespace)?;
        let own = OwnedKeyExpr::try_from(format!("{namespace}/**"))
            .map_err(|_| RouteError(format!("namespace {namespace:?} cannot be extended")))?;

        if sources.is_empty() {
            return refuse("no source key declared".into());
        }
        let mut parsed = Vec::new();
        for source in sources {
            let ke = OwnedKeyExpr::try_from(source.clone()).map_err(|_| {
                RouteError(format!(
                    "source {source:?} is not a canonical key expression"
                ))
            })?;
            let first = source.split('/').next().unwrap_or_default();
            if first.contains('*') || first.contains('$') || first.starts_with('@') {
                return refuse(format!(
                    "source {source:?} must begin with a literal chunk: a gateway reads \
                     declared publishers, never the whole pen"
                ));
            }
            if RESERVED_ROOTS.contains(&first) {
                return refuse(format!(
                    "source {source:?} begins in a reserved namespace ({})",
                    RESERVED_ROOTS.join(", ")
                ));
            }
            if ke.intersects(&own) || ke.intersects(&namespace_ke) {
                return refuse(format!(
                    "source {source:?} intersects the gateway's own namespace {namespace:?}: \
                     it would convert its own output"
                ));
            }
            parsed.push(ke);
        }

        if !template.starts_with("{namespace}/") {
            return refuse(format!(
                "publish key {template:?} must begin with {{namespace}}/: the gateway \
                 writes only beneath its own namespace"
            ));
        }
        let mut rest = template.to_string();
        for p in PLACEHOLDERS {
            rest = rest.replace(p, "");
        }
        if rest.contains('{') || rest.contains('}') || rest.contains('*') || rest.contains('$') {
            return refuse(format!(
                "publish key {template:?} may hold only literal chunks and {}",
                PLACEHOLDERS.join(", ")
            ));
        }
        if !template.contains("{to}") {
            return refuse(format!(
                "publish key {template:?} must contain {{to}}: two target formats may \
                 not share a key"
            ));
        }
        if !template.contains("{source}") && !template.contains("{tail}") {
            return refuse(format!(
                "publish key {template:?} must contain {{source}} or {{tail}}: a result \
                 names the sample it came from"
            ));
        }
        Ok(Self {
            sources: parsed,
            namespace: namespace.to_string(),
            template: template.to_string(),
        })
    }

    /// The declared source key expressions.
    #[must_use]
    pub fn sources(&self) -> &[OwnedKeyExpr] {
        &self.sources
    }

    /// The namespace every result is published beneath.
    #[must_use]
    pub fn namespace(&self) -> &str {
        &self.namespace
    }

    /// Whether a received key is inside a declared source. The pen delivers only what
    /// was subscribed; a sample outside the set is dropped and counted by the caller.
    #[must_use]
    pub fn admits(&self, key: &str) -> bool {
        OwnedKeyExpr::try_from(key.to_string())
            .is_ok_and(|k| self.sources.iter().any(|s| s.includes(&k)))
    }

    /// The key a result is published on, or `None` when the source key yields a key that
    /// is not canonical or leaves the namespace.
    #[must_use]
    pub fn publish_key(&self, source_key: &str, from: &str, to: &str) -> Option<String> {
        let tail = source_key.rsplit('/').next().unwrap_or(source_key);
        let key = self
            .template
            .replace("{namespace}", &self.namespace)
            .replace("{from}", from)
            .replace("{to}", to)
            .replace("{source}", source_key)
            .replace("{tail}", tail);
        if key.contains('*') || key.contains('$') {
            return None;
        }
        let ke = OwnedKeyExpr::try_from(key.clone()).ok()?;
        let own = OwnedKeyExpr::try_from(format!("{}/**", self.namespace)).ok()?;
        (own.includes(&ke) && key != self.namespace).then_some(key)
    }
}

/// The content type a result is published with, by the target format's wire type. A
/// binary format carries its format name as the schema, since `application/protobuf`
/// alone does not say which message.
#[must_use]
pub fn encoding(format: &str, wire: Wire) -> Encoding {
    match wire {
        Wire::Xml => Encoding::APPLICATION_XML,
        Wire::GeoJson => Encoding::from("application/geo+json"),
        Wire::Json => Encoding::APPLICATION_JSON,
        Wire::Protobuf => Encoding::APPLICATION_PROTOBUF.with_schema(format),
        // Tag-length-value records (goat-transformers spec 0.13) are binary, as `bits` is.
        Wire::Bits | Wire::Tlv => Encoding::APPLICATION_OCTET_STREAM.with_schema(format),
        // One record of ASCII text (goat-transformers spec 0.10).
        Wire::Text => Encoding::TEXT_PLAIN.with_schema(format),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn route(sources: &[&str], namespace: &str, template: &str) -> Result<Route, RouteError> {
        let sources: Vec<String> = sources.iter().map(ToString::to_string).collect();
        Route::new(&sources, namespace, template)
    }

    fn why(sources: &[&str], namespace: &str, template: &str) -> String {
        route(sources, namespace, template).unwrap_err().0
    }

    #[test]
    fn the_default_key_keeps_the_whole_source_key_under_the_target_format() {
        let r = route(&["raw/radar1/sapient/*"], "raw/xl1", DEFAULT_TEMPLATE).unwrap();
        assert_eq!(
            r.publish_key("raw/radar1/sapient/192-0-2-50", "sapient", "cot-xml")
                .unwrap(),
            "raw/xl1/cot-xml/raw/radar1/sapient/192-0-2-50"
        );
    }

    #[test]
    fn a_template_may_keep_only_the_source_address_chunk() {
        let r = route(
            &["raw/radar1/**"],
            "raw/xl1",
            "{namespace}/{from}-as-{to}/{tail}",
        )
        .unwrap();
        assert_eq!(
            r.publish_key("raw/radar1/sapient/192-0-2-50", "sapient", "geojson")
                .unwrap(),
            "raw/xl1/sapient-as-geojson/192-0-2-50"
        );
    }

    #[test]
    fn a_source_that_reaches_the_gateways_own_output_is_refused() {
        for source in ["raw/xl1/**", "raw/*/cot-xml/**", "raw/**", "raw/xl1"] {
            assert!(
                why(&[source], "raw/xl1", DEFAULT_TEMPLATE).contains("its own output"),
                "{source}"
            );
        }
    }

    #[test]
    fn a_source_must_name_a_publisher_not_the_whole_pen() {
        for source in ["**", "*/radar1/**", "@/**"] {
            assert!(
                why(&[source], "raw/xl1", DEFAULT_TEMPLATE).contains("literal chunk"),
                "{source}"
            );
        }
        for source in ["downlink/release/lane/x", "ops/pod/health/x"] {
            assert!(
                why(&[source], "raw/xl1", DEFAULT_TEMPLATE).contains("reserved namespace"),
                "{source}"
            );
        }
        assert!(why(&[], "raw/xl1", DEFAULT_TEMPLATE).contains("no source key"));
        assert!(why(&["raw//x"], "raw/xl1", DEFAULT_TEMPLATE).contains("not a canonical"));
    }

    #[test]
    fn the_namespace_is_one_literal_key() {
        assert!(why(&["raw/radar1/**"], "raw/**", DEFAULT_TEMPLATE).contains("must be literal"));
        assert!(why(&["raw/radar1/**"], "", DEFAULT_TEMPLATE).contains("not a canonical"));
    }

    #[test]
    fn a_template_that_could_leave_the_namespace_or_merge_results_is_refused() {
        let s = &["raw/radar1/**"];
        assert!(why(s, "raw/xl1", "cot/{to}/{source}").contains("must begin with"));
        assert!(why(s, "raw/xl1", "{namespace}/{source}").contains("must contain {to}"));
        assert!(why(s, "raw/xl1", "{namespace}/{to}").contains("{source} or {tail}"));
        assert!(why(s, "raw/xl1", "{namespace}/{to}/{id}/{tail}").contains("only literal chunks"));
        assert!(why(s, "raw/xl1", "{namespace}/{to}/**/{tail}").contains("only literal chunks"));
    }

    #[test]
    fn only_keys_inside_a_declared_source_are_admitted() {
        let r = route(
            &["raw/radar1/sapient/*", "raw/tak/cot/**"],
            "raw/xl1",
            DEFAULT_TEMPLATE,
        )
        .unwrap();
        assert!(r.admits("raw/radar1/sapient/192-0-2-50"));
        assert!(r.admits("raw/tak/cot/a/b"));
        assert!(!r.admits("raw/radar1/sapient-session/192-0-2-50"));
        assert!(!r.admits("raw/radar2/sapient/192-0-2-50"));
    }

    #[test]
    fn each_wire_type_has_a_content_type() {
        assert_eq!(encoding("cot-xml", Wire::Xml), Encoding::APPLICATION_XML);
        assert_eq!(
            encoding("geojson", Wire::GeoJson).to_string(),
            "application/geo+json"
        );
        assert_eq!(
            encoding("sapient", Wire::Protobuf).to_string(),
            "application/protobuf;sapient"
        );
        assert_eq!(
            encoding("adsb", Wire::Bits).to_string(),
            "application/octet-stream;adsb"
        );
        assert_eq!(encoding("x", Wire::Json), Encoding::APPLICATION_JSON);
        assert_eq!(
            encoding("misb0601", Wire::Tlv).to_string(),
            Encoding::APPLICATION_OCTET_STREAM
                .with_schema("misb0601")
                .to_string()
        );
        assert_eq!(
            encoding("nmea0183", Wire::Text).to_string(),
            Encoding::TEXT_PLAIN.with_schema("nmea0183").to_string()
        );
    }
}
