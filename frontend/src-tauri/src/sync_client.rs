//! Bounded Sync v1 transport. No redirects, no token-bearing URLs, no implicit retries.
use crate::{
    sync_state::{Binding, Job},
    workspace::Workspace,
};
use reqwest::{Client, Method, Url};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::{
    io::{Read, Seek, SeekFrom},
    sync::{Arc, Mutex},
    time::Duration,
};
use zeroize::Zeroizing;

#[derive(Debug)]
pub struct SyncError {
    pub code: String,
    pub status: u16,
    pub retry_after: Option<u64>,
}
impl SyncError {
    pub(crate) fn new(code: &str) -> Self {
        Self {
            code: code.into(),
            status: 0,
            retry_after: None,
        }
    }
}
type Result<T> = std::result::Result<T, SyncError>;
struct Attempt<'a, W: WorkspaceAccess> {
    workspace: &'a W,
    job: &'a Job,
    finished: bool,
}
impl<W: WorkspaceAccess> Drop for Attempt<'_, W> {
    fn drop(&mut self) {
        if !self.finished {
            let _ = self
                .workspace
                .access(|ws| ws.sync_attempt_interrupt(self.job));
        }
    }
}
pub trait WorkspaceAccess: Send + Sync {
    fn access<T>(
        &self,
        action: impl FnOnce(&mut Workspace) -> crate::workspace::Result<T>,
    ) -> Result<T>;
}
impl WorkspaceAccess for Arc<Mutex<Workspace>> {
    fn access<T>(
        &self,
        action: impl FnOnce(&mut Workspace) -> crate::workspace::Result<T>,
    ) -> Result<T> {
        action(&mut *self.lock().map_err(|_| SyncError::new("HOST_BUSY"))?).map_err(Into::into)
    }
}
impl WorkspaceAccess for Arc<Mutex<Option<Workspace>>> {
    fn access<T>(
        &self,
        action: impl FnOnce(&mut Workspace) -> crate::workspace::Result<T>,
    ) -> Result<T> {
        action(
            self.lock()
                .map_err(|_| SyncError::new("HOST_BUSY"))?
                .as_mut()
                .ok_or_else(|| SyncError::new("WORKSPACE_NOT_OPEN"))?,
        )
        .map_err(Into::into)
    }
}
impl From<crate::workspace::HostError> for SyncError {
    fn from(value: crate::workspace::HostError) -> Self {
        Self::new(&value.code)
    }
}
impl From<std::io::Error> for SyncError {
    fn from(_: std::io::Error) -> Self {
        Self::new("SYNC_IO_FAILED")
    }
}

/// Persist only via the Stronghold Sync scope, never as an IPC response.
#[derive(Serialize, Deserialize)]
pub struct Session {
    pub access_token: String,
    pub refresh_token: String,
    pub expires_in: u64,
    pub device_id: String,
}
impl Drop for Session {
    fn drop(&mut self) {
        use zeroize::Zeroize;
        self.access_token.zeroize();
        self.refresh_token.zeroize();
    }
}
pub struct SyncClient {
    endpoint: Url,
    client: Client,
    token: Zeroizing<String>,
}

impl SyncClient {
    pub fn new(endpoint: &str, token: Zeroizing<String>, allow_test_http: bool) -> Result<Self> {
        let mut url = Url::parse(endpoint).map_err(|_| SyncError::new("SYNC_ENDPOINT_INVALID"))?;
        if (url.scheme() != "https" && !(allow_test_http && url.scheme() == "http"))
            || url.host_str().is_none()
            || !url.username().is_empty()
            || url.password().is_some()
            || url.query().is_some()
            || url.fragment().is_some()
            || !matches!(url.path(), "" | "/")
        {
            return Err(SyncError::new("SYNC_ENDPOINT_INVALID"));
        }
        url.set_path("/");
        let client = Client::builder()
            .timeout(Duration::from_secs(30))
            .redirect(reqwest::redirect::Policy::none())
            .build()
            .map_err(|_| SyncError::new("SYNC_CLIENT_FAILED"))?;
        Ok(Self {
            endpoint: url,
            client,
            token,
        })
    }
    pub async fn json(&self, method: Method, path: &str, body: Option<Value>) -> Result<Value> {
        self.send(method, path, body.map(|v| v.to_string().into_bytes()), true)
            .await
    }
    async fn send(
        &self,
        method: Method,
        path: &str,
        body: Option<Vec<u8>>,
        is_json: bool,
    ) -> Result<Value> {
        if !path.starts_with("sync/v1/") || path.contains(['\\', '#']) || path.contains("..") {
            return Err(SyncError::new("SYNC_PATH_INVALID"));
        }
        let url = self
            .endpoint
            .join(path)
            .map_err(|_| SyncError::new("SYNC_PATH_INVALID"))?;
        let mut request = self.client.request(method, url);
        if !self.token.is_empty() {
            request = request.bearer_auth(self.token.as_str());
        }
        if let Some(body) = body {
            request = request
                .header(
                    "Content-Type",
                    if is_json {
                        "application/json"
                    } else {
                        "application/octet-stream"
                    },
                )
                .body(body);
        }
        let mut response = request
            .send()
            .await
            .map_err(|_| SyncError::new("SYNC_NETWORK_ERROR"))?;
        let status = response.status().as_u16();
        let retry_after = response
            .headers()
            .get("retry-after")
            .and_then(|v| v.to_str().ok())
            .and_then(|v| v.parse().ok());
        let mut body = Vec::new();
        while let Some(chunk) = response
            .chunk()
            .await
            .map_err(|_| SyncError::new("SYNC_NETWORK_ERROR"))?
        {
            if body.len() + chunk.len() > 4 * 1024 * 1024 {
                return Err(SyncError::new("SYNC_RESPONSE_TOO_LARGE"));
            }
            body.extend_from_slice(&chunk);
        }
        let value: Value = if body.is_empty() {
            Value::Null
        } else {
            serde_json::from_slice(&body).map_err(|_| SyncError::new("SYNC_RESPONSE_INVALID"))?
        };
        if !(200..300).contains(&status) {
            let code = value["error"]["code"]
                .as_str()
                .filter(|v| v.len() <= 80 && v.bytes().all(|b| b.is_ascii_uppercase() || b == b'_'))
                .unwrap_or("SYNC_HTTP_ERROR");
            return Err(SyncError {
                code: code.into(),
                status,
                retry_after,
            });
        }
        Ok(value)
    }
    pub async fn login(
        &self,
        username: &str,
        password: Zeroizing<String>,
        device_name: &str,
    ) -> Result<Session> {
        let value = self.json(Method::POST, "sync/v1/auth/sessions", Some(json!({"username":username,"password":password.as_str(),"device_name":device_name}))).await?;
        serde_json::from_value(value).map_err(|_| SyncError::new("SYNC_RESPONSE_INVALID"))
    }
    pub fn endpoint(&self) -> &str {
        self.endpoint.as_str()
    }
    pub async fn refresh(&self, refresh_token: &str) -> Result<Session> {
        let value = self
            .json(
                Method::POST,
                "sync/v1/auth/refresh",
                Some(json!({"refresh_token":refresh_token})),
            )
            .await?;
        serde_json::from_value(value).map_err(|_| SyncError::new("SYNC_RESPONSE_INVALID"))
    }
    pub async fn handshake(&self) -> Result<()> {
        let result = self
            .json(Method::GET, "sync/v1/handshake?protocol=1", None)
            .await?;
        if result["protocol"] != 1
            || result["chunk_size"] != 1048576
            || result["max_object_size"] != 104857600
        {
            return Err(SyncError::new("PROTOCOL_INCOMPATIBLE"));
        }
        Ok(())
    }
    pub async fn snapshot(&self, remote_vault: &str) -> Result<crate::sync_initial::Snapshot> {
        identifier(remote_vault)?;
        let mut cursor = 0i64;
        let mut boundary = None;
        let mut heads = std::collections::BTreeMap::new();
        loop {
            let mut path =
                format!("sync/v1/vaults/{remote_vault}/changes?cursor={cursor}&limit=500");
            if let Some(end) = boundary {
                path.push_str(&format!("&boundary={end}"));
            }
            let page = self.json(Method::GET, &path, None).await?;
            let end = page["boundary"]
                .as_i64()
                .ok_or_else(|| SyncError::new("SYNC_RESPONSE_INVALID"))?;
            if end < cursor || boundary.is_some_and(|old| old != end) || end > 100000 {
                return Err(SyncError::new("SYNC_SNAPSHOT_LIMIT"));
            }
            boundary = Some(end);
            let items = page["items"]
                .as_array()
                .filter(|v| v.len() <= 500)
                .ok_or_else(|| SyncError::new("SYNC_RESPONSE_INVALID"))?;
            if items.is_empty() && cursor != end {
                return Err(SyncError::new("SYNC_RESPONSE_INVALID"));
            }
            for item in items {
                let revision: crate::sync_inbox::RemoteRevision =
                    serde_json::from_value(item.clone())
                        .map_err(|_| SyncError::new("SYNC_RESPONSE_INVALID"))?;
                if revision.sequence != cursor + 1 || revision.sequence > end {
                    return Err(SyncError::new("SYNC_RESPONSE_INVALID"));
                }
                cursor = revision.sequence;
                heads.insert(revision.file_id.clone(), revision);
            }
            if cursor == end {
                return Ok(crate::sync_initial::Snapshot {
                    boundary: end,
                    items: heads.into_values().collect(),
                });
            }
        }
    }
    pub async fn verify_empty(&self, remote_vault: &str) -> Result<()> {
        identifier(remote_vault)?;
        let page = self
            .json(
                Method::GET,
                &format!("sync/v1/vaults/{remote_vault}/changes?limit=1"),
                None,
            )
            .await?;
        if page["boundary"] != 0 || page["items"].as_array().is_none_or(|v| !v.is_empty()) {
            return Err(SyncError::new("SYNC_RECONCILIATION_REQUIRED"));
        }
        Ok(())
    }
    pub async fn push_one(
        &self,
        workspace: &impl WorkspaceAccess,
        binding: &Binding,
    ) -> Result<bool> {
        if Url::parse(&binding.endpoint)
            .ok()
            .is_none_or(|url| url != self.endpoint)
        {
            return Err(SyncError::new("SYNC_BINDING_CHANGED"));
        }
        identifier(&binding.remote_vault)?;
        if workspace
            .access(|ws| ws.sync_initial_pending(&binding.id))?
            .is_some()
        {
            return Ok(false);
        }
        let job = workspace.access(|ws| {
            ws.sync_resume_resolutions(&binding.id)?;
            ws.sync_capture(&binding.id)?;
            ws.sync_next(&binding.id)
        })?;
        let Some(job) = job else {
            return Ok(false);
        };
        if job.state == "conflict" {
            return Err(SyncError::new("REVISION_CONFLICT"));
        }
        workspace.access(|ws| ws.sync_attempt_start(&job))?;
        let mut attempt = Attempt {
            workspace,
            job: &job,
            finished: false,
        };
        let result = async {
            if job.operation == "put" && job.base_revision.is_none() {
                self.upload(workspace, binding, &job).await?;
            }
            let payload = workspace.access(|ws| ws.sync_commit_payload(&job))?;
            let revision = self
                .json(
                    Method::POST,
                    &format!("sync/v1/vaults/{}/revisions", binding.remote_vault),
                    Some(payload),
                )
                .await?;
            workspace.access(|ws| ws.sync_ack(&job, &revision))?;
            Ok(true)
        }
        .await;
        workspace.access(|ws| {
            ws.sync_attempt_finish(
                &job,
                result.as_ref().err().map(|e: &SyncError| e.code.as_str()),
            )
        })?;
        attempt.finished = true;
        result
    }
    pub async fn pull_page(
        &self,
        workspace: &impl WorkspaceAccess,
        binding: &Binding,
    ) -> Result<usize> {
        if Url::parse(&binding.endpoint)
            .ok()
            .is_none_or(|url| url != self.endpoint)
        {
            return Err(SyncError::new("SYNC_BINDING_CHANGED"));
        }
        identifier(&binding.remote_vault)?;
        workspace.access(|ws| {
            ws.sync_resume_resolutions(&binding.id)?;
            while ws.sync_apply_pending(&binding.id)? {}
            Ok(())
        })?;
        if let Some(initial) = workspace.access(|ws| ws.sync_initial_pending(&binding.id))? {
            let count = initial.len().min(100);
            for revision in initial.iter().take(count) {
                if revision.operation == "put"
                    && workspace.access(|ws| ws.sync_path_enabled(&revision.path))?
                {
                    self.download(workspace, binding, revision).await?;
                }
                workspace.access(|ws| {
                    ws.sync_stage(&binding.id, revision)?;
                    ws.sync_apply_pending(&binding.id)?;
                    Ok(())
                })?;
            }
            return Ok(count);
        }
        let (cursor, boundary) = workspace.access(|ws| {
            ws.check_binding(&binding.id)?;
            Ok((
                ws.sync_binding()?
                    .ok_or_else(|| crate::workspace::HostError::new("SYNC_BINDING_CHANGED"))?
                    .cursor,
                ws.sync_boundary(&binding.id)?,
            ))
        })?;
        let mut path = format!(
            "sync/v1/vaults/{}/changes?cursor={cursor}&limit=100",
            binding.remote_vault
        );
        if let Some(end) = boundary {
            path.push_str(&format!("&boundary={end}"));
        }
        let page = self.json(Method::GET, &path, None).await?;
        let end = page["boundary"]
            .as_i64()
            .ok_or_else(|| SyncError::new("SYNC_RESPONSE_INVALID"))?;
        let items = page["items"]
            .as_array()
            .filter(|items| items.len() <= 100)
            .ok_or_else(|| SyncError::new("SYNC_RESPONSE_INVALID"))?;
        if items.is_empty() {
            if end != cursor {
                return Err(SyncError::new("SYNC_RESPONSE_INVALID"));
            }
            return Ok(0);
        }
        workspace.access(|ws| ws.sync_set_boundary(&binding.id, end))?;
        for (index, item) in items.iter().enumerate() {
            let revision: crate::sync_inbox::RemoteRevision = serde_json::from_value(item.clone())
                .map_err(|_| SyncError::new("SYNC_RESPONSE_INVALID"))?;
            revision.validate(binding)?;
            if revision.sequence != cursor + index as i64 + 1 || revision.sequence > end {
                return Err(SyncError::new("SYNC_RESPONSE_INVALID"));
            }
            if revision.operation == "put"
                && workspace.access(|ws| ws.sync_path_enabled(&revision.path))?
            {
                self.download(workspace, binding, &revision).await?;
            }
            workspace.access(|ws| {
                ws.sync_stage(&binding.id, &revision)?;
                ws.sync_apply_pending(&binding.id)?;
                Ok(())
            })?;
        }
        Ok(items.len())
    }
    async fn download(
        &self,
        workspace: &impl WorkspaceAccess,
        binding: &Binding,
        revision: &crate::sync_inbox::RemoteRevision,
    ) -> Result<()> {
        let digest = revision
            .hash
            .as_deref()
            .ok_or_else(|| SyncError::new("SYNC_RESPONSE_INVALID"))?;
        let target = workspace.access(|ws| {
            ws.check_binding(&binding.id)?;
            ws.sync_spool(digest)
        })?;
        if target.exists() {
            crate::payloads::verify(&target, digest, revision.size as u64)?;
            workspace.access(|ws| ws.check_binding(&binding.id))?;
            return Ok(());
        }
        let url = self
            .endpoint
            .join(&format!(
                "sync/v1/vaults/{}/objects/{digest}",
                binding.remote_vault
            ))
            .map_err(|_| SyncError::new("SYNC_PATH_INVALID"))?;
        let mut response = self
            .client
            .get(url)
            .bearer_auth(self.token.as_str())
            .send()
            .await
            .map_err(|_| SyncError::new("SYNC_NETWORK_ERROR"))?;
        if !response.status().is_success() {
            return Err(SyncError {
                code: "SYNC_DOWNLOAD_FAILED".into(),
                status: response.status().as_u16(),
                retry_after: None,
            });
        }
        let mut file = tempfile::NamedTempFile::new_in(
            target
                .parent()
                .ok_or_else(|| SyncError::new("SYNC_SPOOL_FAILED"))?,
        )?;
        let mut hasher = Sha256::new();
        let mut length = 0u64;
        while let Some(chunk) = response
            .chunk()
            .await
            .map_err(|_| SyncError::new("SYNC_NETWORK_ERROR"))?
        {
            length += chunk.len() as u64;
            if length > revision.size as u64 {
                return Err(SyncError::new("SYNC_OBJECT_CORRUPT"));
            }
            workspace.access(|ws| ws.check_binding(&binding.id))?;
            std::io::Write::write_all(&mut file, &chunk)?;
            hasher.update(&chunk);
        }
        if length != revision.size as u64 || format!("{:x}", hasher.finalize()) != digest {
            return Err(SyncError::new("SYNC_OBJECT_CORRUPT"));
        }
        file.as_file().sync_all()?;
        file.persist_noclobber(target)
            .map_err(|_| SyncError::new("SYNC_SPOOL_FAILED"))?;
        Ok(())
    }
    async fn upload(
        &self,
        workspace: &impl WorkspaceAccess,
        binding: &Binding,
        job: &Job,
    ) -> Result<()> {
        let path = workspace.access(|ws| ws.sync_spool(&job.hash))?;
        let mut file = crate::payloads::open_verified(&path, &job.hash, job.size as u64)?;
        workspace.access(|ws| ws.check_binding(&binding.id))?;
        let mut buffer = vec![0u8; 1048576];
        let base = format!("sync/v1/vaults/{}/uploads", binding.remote_vault);
        let mut upload_id = job.upload_id.clone();
        let mut offset = 0;
        if let Some(id) = &upload_id {
            identifier(id)?;
            match self.json(Method::GET, &format!("{base}/{id}"), None).await {
                Ok(status) => {
                    offset = status["offset"]
                        .as_u64()
                        .filter(|v| *v <= job.size as u64)
                        .ok_or_else(|| SyncError::new("SYNC_RESPONSE_INVALID"))?
                }
                Err(error)
                    if matches!(error.code.as_str(), "UPLOAD_EXPIRED" | "UPLOAD_DAMAGED") =>
                {
                    upload_id = None
                }
                Err(error) => return Err(error),
            }
        }
        if upload_id.is_none() {
            let response = self
                .json(
                    Method::POST,
                    &base,
                    Some(json!({"content_hash":job.hash,"size":job.size})),
                )
                .await?;
            if response["complete"] == true {
                return Ok(());
            }
            upload_id = Some(
                response["upload_id"]
                    .as_str()
                    .ok_or_else(|| SyncError::new("SYNC_RESPONSE_INVALID"))?
                    .into(),
            );
            workspace.access(|ws| ws.sync_upload(job, upload_id.as_deref()))?;
        }
        let id = upload_id.ok_or_else(|| SyncError::new("SYNC_RESPONSE_INVALID"))?;
        identifier(&id)?;
        file.seek(SeekFrom::Start(offset))?;
        while offset < job.size as u64 {
            workspace.access(|ws| ws.check_binding(&binding.id))?;
            let count = file.read(&mut buffer)?;
            if count == 0 {
                return Err(SyncError::new("SYNC_SPOOL_CORRUPT"));
            }
            let value = self
                .send(
                    Method::PUT,
                    &format!("{base}/{id}?offset={offset}"),
                    Some(buffer[..count].to_vec()),
                    false,
                )
                .await?;
            if value["offset"].as_u64() != Some(offset + count as u64) {
                return Err(SyncError::new("SYNC_RESPONSE_INVALID"));
            }
            offset += count as u64;
        }
        let value = self
            .json(Method::POST, &format!("{base}/{id}/complete"), None)
            .await?;
        if value["complete"] != true || value["content_hash"] != job.hash {
            return Err(SyncError::new("SYNC_RESPONSE_INVALID"));
        }
        Ok(())
    }
}
fn identifier(value: &str) -> Result<()> {
    if value.is_empty()
        || value.len() > 80
        || !value
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'-')
    {
        return Err(SyncError::new("SYNC_IDENTIFIER_INVALID"));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[tokio::test]
    async fn dropping_a_pending_attempt_marks_interruption_without_restart() {
        let dir = tempfile::tempdir().unwrap();
        let mut ws = Workspace::open(dir.path()).unwrap();
        ws.write("cancel.md", "", b"retained", "local").unwrap();
        let binding = ws
            .sync_bind_empty("https://sync.example", "remote", "account")
            .unwrap();
        let job = ws.sync_next(&binding.id).unwrap().unwrap();
        let workspace = Arc::new(Mutex::new(ws));
        let future = async {
            workspace.access(|ws| ws.sync_attempt_start(&job)).unwrap();
            let _attempt = Attempt {
                workspace: &workspace,
                job: &job,
                finished: false,
            };
            std::future::pending::<()>().await;
        };
        assert!(tokio::time::timeout(Duration::from_millis(10), future)
            .await
            .is_err());
        let rows = workspace
            .access(|ws| ws.sync_attempts(&binding.id))
            .unwrap();
        assert_eq!(rows[0]["outcome"], "interrupted");
        assert_eq!(rows[0]["attempts"], 1);
        assert_eq!(
            workspace.access(|ws| ws.read("cancel.md")).unwrap().content,
            "retained"
        );
    }
}
