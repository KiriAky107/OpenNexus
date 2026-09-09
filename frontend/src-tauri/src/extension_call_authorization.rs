//! Host 内存中的调用审核。UI 或注册表必须先确认用户确已授权，再执行确认；
//! 此处不提供渲染进程命令，也不设置自动授权策略。
use crate::{
    extension_mcp_tools::{Description, Tool},
    extension_permit::{Claims, ExecutionKind},
    workspace::{HostError, Result},
};
use serde::Serialize;
use serde_json::Value;
use sha2::{Digest, Sha256};
use std::{
    collections::{BTreeMap, BTreeSet},
    time::{Duration, Instant},
};
use zeroize::Zeroizing;
#[derive(Clone, Serialize)]
pub struct Identity {
    instance_id: String,
    kind: ExecutionKind,
    source: String,
    namespace: String,
    package_id: String,
    version: String,
    vault_id: String,
    permissions: BTreeSet<String>,
    execution_digest: String,
}
impl Identity {
    /// 仅在启动许可/条目/上下文验证后调用。
    pub(crate) fn from_claims(claims: &Claims) -> Result<Self> {
        let bytes = Zeroizing::new(
            serde_json::to_vec(claims)
                .map_err(|_| HostError::new("EXTENSION_CALL_BINDING_INVALID"))?,
        );
        let mut hash = Sha256::new();
        hash.update(b"OpenNexus execution identity v1\0");
        hash.update(bytes.as_slice());
        Ok(Self {
            instance_id: uuid::Uuid::new_v4().to_string(),
            kind: claims.kind,
            source: claims.source.clone(),
            namespace: claims.namespace.clone(),
            package_id: claims.package_id.clone(),
            version: claims.version.clone(),
            vault_id: claims.vault_id.clone(),
            permissions: claims.permissions.clone(),
            execution_digest: format!("{:x}", hash.finalize()),
        })
    }
}
#[derive(Serialize)]
pub struct Review {
    pub review_id: String,
    pub identity: Identity,
    pub tool: Description,
    pub arguments: Value,
    pub contract_digest: String,
    pub valid_for_seconds: u64,
}
struct Pending {
    name: String,
    arguments: Value,
    contract_digest: String,
    expires: Instant,
    bytes: usize,
}
/// 进程内、不可克隆、不可序列化、单次消耗功能。审核确认后，工具名称和参数无法更换。
pub struct ApprovedCall {
    instance: String,
    epoch: String,
    call: Pending,
}
pub(crate) struct Invocation {
    pub name: String,
    pub arguments: Value,
    pub contract_digest: String,
}
pub(crate) struct Gate {
    identity: Identity,
    epoch: String,
    pending: BTreeMap<String, Pending>,
}
impl Gate {
    pub(crate) fn new(identity: Identity) -> Self {
        Self {
            identity,
            epoch: uuid::Uuid::new_v4().to_string(),
            pending: BTreeMap::new(),
        }
    }
    pub(crate) fn invalidate(&mut self) {
        self.epoch = uuid::Uuid::new_v4().to_string();
        self.pending.clear();
    }
    pub(crate) fn review(&mut self, tool: &Tool, arguments: Value) -> Result<Review> {
        tool.validate_arguments(&arguments)?;
        self.pending
            .retain(|_, pending| pending.expires > Instant::now());
        let bytes = serde_json::to_vec(&arguments)
            .map_err(|_| HostError::new("EXTENSION_CALL_REVIEW_INVALID"))?
            .len();
        if self.pending.len() >= 64
            || self.pending.values().map(|p| p.bytes).sum::<usize>() + bytes > 2 * 1024 * 1024
        {
            return Err(HostError::new("EXTENSION_CALL_REVIEW_LIMIT"));
        }
        let tool = tool.description();
        let contract_digest = contract_digest(&tool)?;
        let review_id = uuid::Uuid::new_v4().to_string();
        self.pending.insert(
            review_id.clone(),
            Pending {
                name: tool.name.clone(),
                arguments: arguments.clone(),
                contract_digest: contract_digest.clone(),
                expires: Instant::now() + Duration::from_secs(120),
                bytes,
            },
        );
        Ok(Review {
            review_id,
            identity: self.identity.clone(),
            tool,
            arguments,
            contract_digest,
            valid_for_seconds: 120,
        })
    }
    /// 经过身份验证的 Host 批准路线必​​须首先验证用户同意。
    pub(crate) fn confirm(&mut self, review_id: &str) -> Result<ApprovedCall> {
        let call = self
            .pending
            .remove(review_id)
            .ok_or_else(|| HostError::new("EXTENSION_CALL_REVIEW_UNKNOWN"))?;
        if call.expires <= Instant::now() {
            return Err(HostError::new("EXTENSION_CALL_REVIEW_EXPIRED"));
        }
        Ok(ApprovedCall {
            instance: self.identity.instance_id.clone(),
            epoch: self.epoch.clone(),
            call,
        })
    }
    pub(crate) fn consume(&self, approved: ApprovedCall) -> Result<Invocation> {
        if approved.instance != self.identity.instance_id {
            return Err(HostError::new("EXTENSION_CALL_INSTANCE_MISMATCH"));
        }
        if approved.epoch != self.epoch {
            return Err(HostError::new("EXTENSION_CALL_CATALOG_CHANGED"));
        }
        if approved.call.expires <= Instant::now() {
            return Err(HostError::new("EXTENSION_CALL_REVIEW_EXPIRED"));
        }
        Ok(Invocation {
            name: approved.call.name,
            arguments: approved.call.arguments,
            contract_digest: approved.call.contract_digest,
        })
    }
}
pub(crate) fn contract_digest(tool: &Description) -> Result<String> {
    let mut hash = Sha256::new();
    hash.update(b"OpenNexus MCP tool contract v1\0");
    hash.update(
        serde_json::to_vec(tool).map_err(|_| HostError::new("EXTENSION_CALL_REVIEW_INVALID"))?,
    );
    Ok(format!("{:x}", hash.finalize()))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::extension_mcp_tools::Catalog;
    use serde_json::json;
    fn identity() -> Identity {
        Identity {
            instance_id: uuid::Uuid::new_v4().to_string(),
            kind: ExecutionKind::Mcp,
            source: "https://catalog.example/".into(),
            namespace: "examples".into(),
            package_id: "echo".into(),
            version: "1.0.0".into(),
            vault_id: uuid::Uuid::new_v4().to_string(),
            permissions: BTreeSet::new(),
            execution_digest: "a".repeat(64),
        }
    }
    fn tool() -> std::sync::Arc<Tool> {
        Catalog::discover(|_| {
            Ok(json!({"tools":[{"name":"echo","inputSchema":{"type":"object"}}]}))
        })
        .unwrap()
        .tool("echo")
        .unwrap()
    }
    #[test]
    fn review_freezes_arguments_and_is_single_use_instance_epoch_and_expiry_bound() {
        let mut gate = Gate::new(identity());
        let tool = tool();
        let mut review = gate.review(&tool, json!({"value":"original"})).unwrap();
        review.arguments = json!({"value":"tampered"});
        review.tool.name = "tampered".into();
        let approved = gate.confirm(&review.review_id).unwrap();
        assert_eq!(
            gate.confirm(&review.review_id).err().unwrap().code,
            "EXTENSION_CALL_REVIEW_UNKNOWN"
        );
        let call = gate.consume(approved).unwrap();
        assert_eq!(call.name, "echo");
        assert_eq!(call.arguments, json!({"value":"original"}));
        assert_eq!(
            call.contract_digest,
            contract_digest(&tool.description()).unwrap()
        );
        let review = gate.review(&tool, json!({})).unwrap();
        let approved = gate.confirm(&review.review_id).unwrap();
        assert_eq!(
            Gate::new(identity()).consume(approved).err().unwrap().code,
            "EXTENSION_CALL_INSTANCE_MISMATCH"
        );
        let review = gate.review(&tool, json!({})).unwrap();
        let approved = gate.confirm(&review.review_id).unwrap();
        let pending = gate.review(&tool, json!({})).unwrap();
        gate.invalidate();
        assert_eq!(
            gate.consume(approved).err().unwrap().code,
            "EXTENSION_CALL_CATALOG_CHANGED"
        );
        assert_eq!(
            gate.confirm(&pending.review_id).err().unwrap().code,
            "EXTENSION_CALL_REVIEW_UNKNOWN"
        );
        let review = gate.review(&tool, json!({})).unwrap();
        gate.pending.get_mut(&review.review_id).unwrap().expires = Instant::now();
        assert_eq!(
            gate.confirm(&review.review_id).err().unwrap().code,
            "EXTENSION_CALL_REVIEW_EXPIRED"
        );
        let review = gate.review(&tool, json!({})).unwrap();
        let mut approved = gate.confirm(&review.review_id).unwrap();
        approved.call.expires = Instant::now();
        assert_eq!(
            gate.consume(approved).err().unwrap().code,
            "EXTENSION_CALL_REVIEW_EXPIRED"
        );
    }
    #[test]
    fn pending_review_count_and_bytes_are_bounded_and_invalidated_slots_are_reusable() {
        let tool = tool();
        let mut gate = Gate::new(identity());
        for _ in 0..64 {
            gate.review(&tool, json!({})).unwrap();
        }
        assert_eq!(
            gate.review(&tool, json!({})).err().unwrap().code,
            "EXTENSION_CALL_REVIEW_LIMIT"
        );
        gate.invalidate();
        for _ in 0..10 {
            gate.review(&tool, json!({"value":"x".repeat(200_000)}))
                .unwrap();
        }
        assert_eq!(
            gate.review(&tool, json!({"value":"x".repeat(200_000)}))
                .err()
                .unwrap()
                .code,
            "EXTENSION_CALL_REVIEW_LIMIT"
        );
        for value in gate.pending.values_mut() {
            value.expires = Instant::now();
        }
        gate.review(&tool, json!({"value":"x".repeat(200_000)}))
            .unwrap();
        assert_eq!(gate.pending.len(), 1);
    }
}
