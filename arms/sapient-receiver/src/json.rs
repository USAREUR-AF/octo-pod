//! Descriptor-based protobuf JSON decoding. No hand-maintained JSON/wire schema.
use anyhow::Result;
use prost_reflect::{DescriptorPool, DynamicMessage, MessageDescriptor};
use sapient_proto::v2::SapientMessage;
use std::sync::OnceLock;

pub(crate) fn descriptor() -> MessageDescriptor {
    static POOL: OnceLock<DescriptorPool> = OnceLock::new();
    POOL.get_or_init(|| {
        DescriptorPool::decode(sapient_proto::FILE_DESCRIPTOR_SET)
            .expect("build-generated descriptor is valid")
    })
    .get_message_by_name("sapient_msg.bsi_flex_335_v2_0.SapientMessage")
    .expect("the envelope is present in its own descriptor")
}

/// Parse protobuf JSON, rejecting unknown fields, invalid enums and trailing input.
pub fn decode(source: &str) -> Result<SapientMessage> {
    let mut json = serde_json::Deserializer::from_str(source);
    let dynamic = DynamicMessage::deserialize(descriptor(), &mut json)?;
    json.end()?;
    Ok(dynamic.transcode_to()?)
}
