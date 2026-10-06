//! Fresh approval of installed declarative material, without downloading or activating it.
use super::*;

pub struct VerifiedCandidate {
    pub runtime: RuntimePackage,
    checked: crate::extension_trust::Checked,
    key: [u8; 32],
}
impl VerifiedCandidate {
    pub fn ensure_fresh(&self) -> Result<()> {
        self.checked
            .matches(&self.runtime.source, &self.runtime.release, &self.key)
    }
}
impl ExtensionStore {
    pub async fn candidate_package_online(
        &mut self,
        slot: &str,
        vault: &str,
    ) -> Result<VerifiedCandidate> {
        let material = self.runtime_package(slot, vault, None)?;
        if !matches!(
            material.release.kind.as_str(),
            "persona" | "template" | "model" | "mcp"
        ) {
            return Err(HostError::new("EXTENSION_CANDIDATE_TYPE"));
        }
        let trusted = self
            .trust_setting(
                &material.source,
                &material.release.namespace,
                &material.release.key_id,
            )?
            .ok_or_else(|| HostError::new("EXTENSION_SOURCE_UNTRUSTED"))?;
        let client = crate::extension_trust::Client::new(&material.source)?;
        let checked = client
            .check(
                crate::extension_trust::Pin {
                    source_id: &trusted.source_id,
                    key_id: &trusted.key_id,
                    namespace: &trusted.namespace,
                    public_key: &trusted.public_key,
                },
                &material.release,
            )
            .await;
        let checked = match checked {
            Ok(checked) => checked,
            Err(error) => {
                self.remember_revocation(
                    &material.source,
                    &material.release,
                    &trusted.public_key,
                    &error.code,
                )?;
                return Err(error);
            }
        };
        let runtime = self.runtime_package(slot, vault, None)?;
        if runtime.active != material.active
            || runtime.source != material.source
            || runtime.signer_sha256 != material.signer_sha256
        {
            return Err(HostError::new("EXTENSION_CANDIDATE_CHANGED"));
        }
        let verified = VerifiedCandidate {
            runtime,
            checked,
            key: trusted.public_key,
        };
        verified.ensure_fresh()?;
        Ok(verified)
    }
}
