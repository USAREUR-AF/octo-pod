//! Port of SAPIENT BSI Flex 335 v2.0 Test Harness validators.
//! Crown-owned copyright, 2021-2024. Used under Apache-2.0; see tests/vectors/NOTICE.
//! Rust adaptation: descriptor-backed field access preserves protobuf presence and
//! enum definitions without duplicating the schema. Rules below follow the pinned
//! C# validators, including their presence/default distinctions and omissions.

use prost_reflect::{DynamicMessage, Kind, ReflectMessage, Value};
use sapient_proto::v2::SapientMessage;

/// Reference-harness violations. This is not a guarantee of real-controller acceptance.
pub fn violations(msg: &SapientMessage) -> Vec<String> {
    let mut dynamic = DynamicMessage::new(crate::json::descriptor());
    dynamic
        .transcode_from(msg)
        .expect("shared descriptor matches generated message");
    let mut errors = Vec::new();
    visit(&dynamic, "SapientMessage", &mut errors);
    errors
}

struct Rules<'a> {
    msg: &'a DynamicMessage,
    path: &'a str,
    errors: &'a mut Vec<String>,
}
impl Rules<'_> {
    fn field(&self, name: &str) -> prost_reflect::FieldDescriptor {
        self.msg
            .descriptor()
            .get_field_by_name(name)
            .unwrap_or_else(|| {
                panic!(
                    "validator references missing field {}.{name}",
                    self.msg.descriptor().full_name()
                )
            })
    }
    fn value(&self, name: &str) -> Value {
        self.msg.get_field(&self.field(name)).into_owned()
    }
    fn has(&self, name: &str) -> bool {
        self.msg.has_field(&self.field(name))
    }
    fn error(&mut self, field: &str, rule: &str) {
        self.errors.push(format!("{}.{field}: {rule}", self.path));
    }
    fn present(&mut self, names: &[&str]) {
        for name in names {
            if !self.has(name) {
                self.error(name, "must be present");
            }
        }
    }
    // FluentValidation NotEmpty: zero numeric/enum values and whitespace strings
    // are empty, while NotNull/HasX alone allows explicitly present zero values.
    fn nonempty(&mut self, names: &[&str]) {
        for name in names {
            let empty = match self.value(name) {
                Value::String(v) => v.trim().is_empty(),
                Value::List(v) => v.is_empty(),
                Value::Bytes(v) => v.is_empty(),
                Value::F32(v) => v == 0.0,
                Value::F64(v) => v == 0.0,
                Value::I32(v) | Value::EnumNumber(v) => v == 0,
                Value::I64(v) => v == 0,
                Value::U32(v) => v == 0,
                Value::U64(v) => v == 0,
                Value::Bool(v) => !v,
                Value::Message(_) => !self.has(name),
                Value::Map(v) => v.is_empty(),
            };
            if empty {
                self.error(name, "must not be empty/default");
            }
        }
    }
    fn enums(&mut self, names: &[&str], nonzero: bool) {
        for name in names {
            let Value::EnumNumber(n) = self.value(name) else {
                panic!("not enum: {name}")
            };
            let Kind::Enum(e) = self.field(name).kind() else {
                unreachable!()
            };
            if e.get_value(n).is_none() || (nonzero && n == 0) {
                self.error(name, "invalid or unspecified enum");
            }
        }
    }
    fn text(&self, name: &str) -> String {
        match self.value(name) {
            Value::String(s) => s,
            _ => panic!("not string: {name}"),
        }
    }
    fn ids(&mut self, names: &[&str], ulid: bool) {
        for name in names {
            let text = self.text(name);
            // Reference helpers allow whitespace/empty; mandatory rules are separate.
            if !text.trim().is_empty() && !(if ulid { is_ulid(&text) } else { is_uuid(&text) }) {
                self.error(name, if ulid { "invalid ULID" } else { "invalid UUID" });
            }
        }
    }
    fn scalars(&mut self, names: &[&str]) {
        for name in names {
            let n = match self.value(name) {
                Value::F32(v) => f64::from(v),
                Value::F64(v) => v,
                _ => panic!("not float"),
            };
            if !(0.0..=1.0).contains(&n) {
                self.error(name, "must be in [0,1]");
            }
        }
    }
    fn nonzero(&self, name: &str) -> bool {
        matches!(self.value(name), Value::EnumNumber(n) if n != 0)
    }
    fn xor(&mut self, left: &str, right: &str) {
        if self.has(left) == self.has(right) {
            self.error(left, &format!("exactly one of {left}, {right} is required"));
        }
    }
}

fn is_uuid(s: &str) -> bool {
    uuid::Uuid::parse_str(s.trim()).is_ok()
}
fn is_ulid(s: &str) -> bool {
    s.len() == 26
        && s.bytes()
            .all(|b| b"0123456789ABCDEFGHJKMNPQRSTVWXYZ".contains(&b.to_ascii_uppercase()))
}

fn visit(msg: &DynamicMessage, path: &str, errors: &mut Vec<String>) {
    let descriptor = msg.descriptor();
    let name = descriptor
        .full_name()
        .strip_prefix("sapient_msg.bsi_flex_335_v2_0.")
        .unwrap_or(descriptor.full_name());
    let mut r = Rules { msg, path, errors };
    match name {
        "SapientMessage" => {
            r.present(&["timestamp"]);
            r.nonempty(&["node_id"]);
            r.ids(&["node_id", "destination_id"], false);
            if ![
                "registration",
                "registration_ack",
                "status_report",
                "detection_report",
                "alert",
                "alert_ack",
                "task",
                "task_ack",
                "error",
            ]
            .iter()
            .any(|f| r.has(f))
            {
                r.error("content", "must be present");
            }
        }
        "Registration" => {
            r.nonempty(&[
                "node_definition",
                "icd_version",
                "capabilities",
                "mode_definition",
                "config_data",
            ]);
            r.present(&["icd_version", "status_definition"]);
            if r.text("icd_version") != "BSI Flex 335 v2.0" {
                r.error("icd_version", "must equal BSI Flex 335 v2.0");
            }
            if let Value::List(ids) = r.value("dependent_nodes") {
                for id in ids {
                    if let Value::String(id) = id {
                        if !id.trim().is_empty() && !is_uuid(&id) {
                            r.error("dependent_nodes", "invalid UUID");
                        }
                    }
                }
            }
        }
        "Registration.NodeDefinition" => r.enums(&["node_type"], true),
        "Registration.Capability" => {
            r.present(&["category", "type"]);
            r.nonempty(&["category", "type"]);
        }
        "Registration.StatusDefinition" => r.present(&["status_interval"]),
        "Registration.Duration" => {
            r.present(&["units", "value"]);
            r.enums(&["units"], true);
            r.nonempty(&["value"]);
        }
        "Registration.ModeDefinition" => {
            r.present(&["mode_name", "settle_time", "task"]);
            r.nonempty(&["mode_name"]);
            r.enums(&["mode_type"], true);
        }
        "Registration.TaskDefinition" => r.present(&["concurrent_tasks", "region_definition"]),
        "Registration.RegionDefinition" => {
            r.nonempty(&["region_type", "region_area"]);
            if let Value::List(types) = r.value("region_type") {
                if types.contains(&Value::EnumNumber(0)) {
                    r.error("region_type", "must not be unspecified");
                }
            }
        }
        "Registration.ConfigurationData" => r.nonempty(&["manufacturer", "model"]),
        "Registration.ModeParameter" => {
            r.present(&["type", "value"]);
            r.nonempty(&["type", "value"]);
        }
        "Registration.LocationType" => {
            if path.ends_with(".location_definition") {
                r.present(&["location_units", "location_datum"]);
                r.nonempty(&["location_units", "location_datum"]);
            } else {
                let location = r.has("location_units")
                    && r.has("location_datum")
                    && r.nonzero("location_units")
                    && r.nonzero("location_datum")
                    && !r.has("range_bearing_units")
                    && !r.has("range_bearing_datum");
                let bearing = r.has("range_bearing_units")
                    && r.has("range_bearing_datum")
                    && r.nonzero("range_bearing_units")
                    && r.nonzero("range_bearing_datum")
                    && !r.has("location_units")
                    && !r.has("location_datum");
                if !location && !bearing {
                    r.error(
                        "location_units",
                        "must define either location or range/bearing, not both",
                    );
                }
                let utm = match r.value("location_units") {
                    Value::EnumNumber(n) => {
                        let Kind::Enum(e) = r.field("location_units").kind() else {
                            unreachable!()
                        };
                        e.get_value(n).is_some_and(|v| v.name().ends_with("UTM_M"))
                    }
                    _ => false,
                };
                if utm && (!r.has("zone") || r.text("zone").is_empty()) {
                    r.error("zone", "UTM requires a zone");
                }
            }
        }
        "Registration.VelocityType" => {
            r.present(&["enu_velocity_units"]);
            r.xor("location_datum", "range_bearing_datum");
            if (r.has("location_datum") && !r.nonzero("location_datum"))
                || (r.has("range_bearing_datum") && !r.nonzero("range_bearing_datum"))
            {
                r.error("datum", "must not be unspecified");
            }
        }
        "Registration.StatusReport" => {
            r.present(&["category"]);
            r.enums(&["category"], true);
            r.nonempty(&["type"]);
        }
        "Registration.DetectionDefinition" => r.present(&["location_type"]),
        "Registration.GeometricError" => {
            r.present(&["type", "units", "variation_type"]);
            r.nonempty(&["type", "units", "variation_type"]);
        }
        "Registration.PerformanceValue" => {
            r.present(&["type", "units", "unit_value"]);
            r.nonempty(&["type", "units", "unit_value"]);
        }
        "Registration.DetectionReport" => {
            r.present(&["category", "type", "units"]);
            r.enums(&["category"], true);
            r.nonempty(&["type", "units"]);
        }
        "Registration.DetectionClassDefinition" => r.enums(&["confidence_definition"], false),
        "Registration.ClassDefinition"
        | "Registration.BehaviourDefinition"
        | "Registration.ClassFilterDefinition"
        | "Registration.BehaviourFilterDefinition" => {
            r.present(&["type"]);
            r.nonempty(&["type"]);
        }
        "Registration.SubClass" | "Registration.SubClassFilterDefinition" => {
            r.present(&["type", "level"]);
            r.nonempty(&["type"]);
        }
        "Registration.FilterParameter" => {
            r.present(&["parameter"]);
            r.nonempty(&["parameter", "operators"]);
            if let Value::List(ops) = r.value("operators") {
                if ops.contains(&Value::EnumNumber(0)) {
                    r.error("operators", "must not be unspecified");
                }
            }
        }
        "Registration.Command" => {
            r.present(&["units", "type", "completion_time"]);
            r.nonempty(&["units"]);
            r.enums(&["type"], true);
        }
        "Registration.TaxonomyDockDefinition" => {
            r.present(&["Dock_class"]);
            r.nonempty(&["Dock_class"]);
        }
        "Registration.ExtensionSubclass" => {
            r.present(&["Subclass_namespace", "Subclass_name"]);
            r.nonempty(&["Subclass_namespace", "Subclass_name"]);
        }
        "RegistrationAck" => r.present(&["acceptance"]),
        "StatusReport" => {
            r.present(&["report_id", "system", "info", "mode"]);
            r.nonempty(&["report_id", "mode"]);
            r.ids(&["report_id", "active_task_id"], true);
            r.enums(&["system", "info"], true);
        }
        "StatusReport.Status" => {
            r.present(&["status_type"]);
            r.nonempty(&["status_type"]);
        }
        "Location" => {
            r.present(&["x", "y"]);
            r.enums(&["coordinate_system", "datum"], true);
        }
        "LocationList" => r.nonempty(&["locations"]),
        "RangeBearing" | "RangeBearingCone" => r.enums(&["coordinate_system", "datum"], true),
        "ENUVelocityUnits" => r.enums(&["east_north_rate_units"], true),
        "ENUVelocity" => r.nonempty(&["east_rate", "north_rate"]),
        "DetectionReport" => {
            r.nonempty(&["report_id", "object_id"]);
            r.ids(&["report_id", "object_id", "task_id"], true);
            r.xor("location", "range_bearing");
            r.scalars(&["detection_confidence"]);
        }
        "DetectionReport.PredictedLocation" => r.xor("location", "range_bearing"),
        "DetectionReport.TrackObjectInfo" => r.nonempty(&["type", "value"]),
        "DetectionReport.DetectionReportClassification" => r.nonempty(&["type"]),
        "DetectionReport.SubClass" => {
            r.nonempty(&["type"]);
            r.present(&["level"]);
            r.scalars(&["confidence"]);
        }
        "DetectionReport.Behaviour" => {
            r.nonempty(&["type"]);
            r.scalars(&["confidence"]);
            if ![
                "walking",
                "running",
                "crawling",
                "climbing",
                "digging",
                "throwing",
                "loitering",
                "active",
                "passive",
                "other",
            ]
            .contains(&r.text("type").to_lowercase().as_str())
            {
                r.error("type", "unknown behaviour");
            }
        }
        "DetectionReport.Signal" => r.nonempty(&["amplitude", "centre_frequency"]),
        "DetectionReport.DerivedDetection" => {
            r.present(&["timestamp"]);
            r.nonempty(&["node_id", "object_id"]);
            r.ids(&["node_id"], false);
            r.ids(&["object_id"], true);
        }
        "AssociatedFile" => r.nonempty(&["type", "url"]),
        "AssociatedDetection" => {
            r.nonempty(&["node_id", "object_id"]);
            r.ids(&["node_id"], false);
            r.ids(&["object_id"], true);
            r.enums(&["association_type"], false);
        }
        "Alert" => {
            r.nonempty(&["alert_id"]);
            r.ids(&["alert_id", "region_id"], true);
            r.scalars(&["ranking", "confidence"]);
        }
        "AlertAck" => {
            r.nonempty(&["alert_id"]);
            r.ids(&["alert_id"], true);
            r.enums(&["alert_ack_status"], true);
        }
        "Task" => {
            r.nonempty(&["task_id"]);
            r.ids(&["task_id"], true);
            r.enums(&["control"], true);
        }
        "TaskAck" => {
            r.nonempty(&["task_id"]);
            r.ids(&["task_id"], true);
            r.enums(&["task_status"], true);
        }
        "Task.Region" => {
            r.present(&["type", "region_id", "region_name", "region_area"]);
            r.enums(&["type"], true);
            r.nonempty(&["region_id", "region_name"]);
            r.ids(&["region_id"], true);
        }
        "Task.ClassFilter" | "Task.SubClassFilter" => {
            r.present(&["parameter", "type"]);
            r.nonempty(&["type"]);
        }
        "Task.BehaviourFilter" => r.present(&["parameter"]),
        "Task.Parameter" => {
            r.present(&["name", "operator", "value"]);
            r.nonempty(&["name", "operator", "value"]);
        }
        "Task.Command" => {
            let fields: Vec<_> = msg
                .fields()
                .filter(|(f, _)| f.containing_oneof().is_some_and(|o| o.name() == "command"))
                .collect();
            if fields.is_empty() {
                r.error("command", "must be present");
            }
            for (f, _) in fields {
                match f.name() {
                    "request" | "mode_change" => {
                        if r.text(f.name()).is_empty() {
                            r.error(f.name(), "must not be empty");
                        }
                    }
                    "detection_threshold"
                    | "detection_report_rate"
                    | "classification_threshold" => {
                        if !r.nonzero(f.name()) {
                            r.error(f.name(), "must not be unspecified");
                        }
                    }
                    "look_at" | "move_to" | "patrol" | "follow" => {}
                    _ => r.error(f.name(), "unknown command"),
                }
            }
        }
        "FollowObject" => {
            r.nonempty(&["follow_object_id"]);
            r.ids(&["follow_object_id"], true);
        }
        "Error" => r.nonempty(&["packet", "error_message"]),
        _ => {} // No reference rules for this message (e.g. Power, Timestamp).
    }
    // SetValidator/RuleForEach, including recursive subclasses/configuration trees.
    for (field, value) in msg.fields() {
        let child_path = format!("{path}.{}", field.name());
        match value {
            Value::Message(child) => visit(child, &child_path, errors),
            Value::List(children) => {
                for (i, child) in children.iter().enumerate() {
                    if let Value::Message(child) = child {
                        visit(child, &format!("{child_path}[{i}]"), errors);
                    }
                }
            }
            _ => {}
        }
    }
}
