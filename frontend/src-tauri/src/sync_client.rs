//! 有界同步 v1 传输。没有重定向，没有带有令牌的 URL，没有隐式重试。
use crate::{
    sync_progress::{Direction, Event, Transfer, TransferPhase},
    sync_state::{Binding, Job},
    workspace::Workspace,
};
use reqwest::{Client, Method, Url};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
#[cfg(test)]
use std::io::Read;
use std::{
    sync::{Arc, Mutex},
    time::Duration,
};
use zeroize::Zeroizing;

#[cfg(test)]
#[path = "mobile_sync_access_tests.rs"]
mod mobile_sync_access_tests;
#[cfg(test)]
#[path = "sync_transport_foreground_tests.rs"]
mod transport_foreground_tests;

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
            let _ = self.workspace.interrupt_attempt(self.job);
        }
    }
}
pub trait WorkspaceAccess: Send + Sync {
    fn access<T>(
        &self,
        action: impl FnOnce(&mut Workspace) -> crate::workspace::Result<T>,
    ) -> Result<T>;
    fn discover<'a>(
        &'a self,
        binding: &'a str,
    ) -> impl std::future::Future<Output = Result<usize>> + Send + 'a {
        async move { self.access(|ws| ws.sync_discover(binding)) }
    }
    fn capture<'a>(
        &'a self,
        binding: &'a str,
    ) -> impl std::future::Future<Output = Result<()>> + Send + 'a {
        async move { self.access(|ws| ws.sync_capture(binding)) }
    }
    fn resume_resolutions<'a>(
        &'a self,
        binding: &'a str,
    ) -> impl std::future::Future<Output = Result<()>> + Send + 'a {
        async move { self.access(|ws| ws.sync_resume_resolutions(binding)) }
    }
    fn stage_revision<'a>(
        &'a self,
        binding: &'a str,
        revision: &'a crate::sync_inbox::RemoteRevision,
    ) -> impl std::future::Future<Output = Result<()>> + Send + 'a {
        async move { self.access(|ws| ws.sync_stage(binding, revision)) }
    }
    fn apply_pending<'a>(
        &'a self,
        binding: &'a str,
    ) -> impl std::future::Future<Output = Result<bool>> + Send + 'a {
        async move { self.access(|ws| ws.sync_apply_pending(binding)) }
    }
    fn commit_payload<'a>(
        &'a self,
        job: &'a Job,
    ) -> impl std::future::Future<Output = Result<Value>> + Send + 'a {
        async move { self.access(|ws| ws.sync_commit_payload(job)) }
    }
    fn acknowledge<'a>(
        &'a self,
        job: &'a Job,
        revision: &'a Value,
    ) -> impl std::future::Future<Output = Result<()>> + Send + 'a {
        async move { self.access(|ws| ws.sync_ack(job, revision)) }
    }
    fn start_attempt(&self, job: &Job) -> Result<()> {
        self.access(|ws| ws.sync_attempt_start(job))
    }
    /// Cancellation metadata only. A foreground-scoped port may still mark the
    /// exact original attempt interrupted after it has denied all content work.
    fn interrupt_attempt(&self, job: &Job) -> Result<()> {
        self.access(|ws| ws.sync_attempt_interrupt(job))
    }
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
impl WorkspaceAccess for Arc<Mutex<Option<crate::mobile_workspace::MobileWorkspace>>> {
    fn access<T>(
        &self,
        action: impl FnOnce(&mut Workspace) -> crate::workspace::Result<T>,
    ) -> Result<T> {
        let mut guard = self.lock().map_err(|_| SyncError::new("HOST_BUSY"))?;
        let engine = guard
            .as_mut()
            .ok_or_else(|| SyncError::new("MOBILE_STORAGE_UNAVAILABLE"))?;
        action(&mut engine.workspace).map_err(Into::into)
    }
}
impl From<std::io::Error> for SyncError {
    fn from(_: std::io::Error) -> Self {
        Self::new("SYNC_IO_FAILED")
    }
}

/// 仅由宿主凭据代理持有或保存，绝不作为 IPC 响应。
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
    progress: Option<Arc<dyn Fn(Event) + Send + Sync>>,
    foreground: Option<crate::mobile_sync_foreground::Lease>,
}

pub(crate) fn canonical_endpoint(endpoint: &str, allow_test_http: bool) -> Result<Url> {
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
    Ok(url)
}

impl SyncClient {
    pub fn new(endpoint: &str, token: Zeroizing<String>, allow_test_http: bool) -> Result<Self> {
        let url = canonical_endpoint(endpoint, allow_test_http)?;
        let builder = Client::builder()
            .timeout(Duration::from_secs(30))
            .redirect(reqwest::redirect::Policy::none());
        let client = crate::sync_tls::configure(builder)?
            .build()
            .map_err(|_| SyncError::new("SYNC_CLIENT_FAILED"))?;
        Ok(Self {
            endpoint: url,
            client,
            token,
            progress: None,
            foreground: None,
        })
    }
    // Native-only, nonserde and set by the authenticated issuing-device gate.
    // The original lease remains attached even if a native caller retains this
    // client beyond its action; no WebView URL/method/token hook is added.
    pub(crate) fn with_foreground(mut self, lease: crate::mobile_sync_foreground::Lease) -> Self {
        self.foreground = Some(lease);
        self
    }
    fn check_foreground(&self) -> Result<()> {
        if let Some(lease) = &self.foreground {
            lease.check()?;
        }
        Ok(())
    }
    async fn guard_transport<T>(
        &self,
        action: impl std::future::Future<Output = Result<T>>,
    ) -> Result<T> {
        if let Some(lease) = &self.foreground {
            lease.guard(action).await
        } else {
            action.await
        }
    }
    async fn io<T: Send + 'static>(
        &self,
        action: impl FnOnce(&dyn Fn() -> crate::workspace::Result<()>) -> Result<T> + Send + 'static,
    ) -> Result<T> {
        let foreground = self.foreground.clone();
        tokio::task::spawn_blocking(move || {
            let check = || {
                if let Some(lease) = &foreground {
                    lease
                        .check()
                        .map_err(|e| crate::workspace::HostError::new(&e.code))?;
                }
                Ok(())
            };
            check()?;
            let value = action(&check)?;
            check()?;
            Ok(value)
        })
        .await
        .map_err(|_| SyncError::new("SYNC_WORKER_INTERRUPTED"))?
    }
    pub fn with_progress(mut self, progress: Arc<dyn Fn(Event) + Send + Sync>) -> Self {
        self.progress = Some(progress);
        self
    }
    fn report(&self, event: Event) {
        if let Some(progress) = &self.progress {
            progress(event);
        }
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
        self.check_foreground()?;
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
        self.check_foreground()?;
        let mut response = self
            .guard_transport(async {
                request
                    .send()
                    .await
                    .map_err(|_| SyncError::new("SYNC_NETWORK_ERROR"))
            })
            .await?;
        let status = response.status().as_u16();
        let retry_after = response
            .headers()
            .get("retry-after")
            .and_then(|v| v.to_str().ok())
            .and_then(|v| v.parse().ok());
        let mut body = Vec::new();
        while let Some(chunk) = self
            .guard_transport(async {
                response
                    .chunk()
                    .await
                    .map_err(|_| SyncError::new("SYNC_NETWORK_ERROR"))
            })
            .await?
        {
            if body.len() + chunk.len() > 4 * 1024 * 1024 {
                return Err(SyncError::new("SYNC_RESPONSE_TOO_LARGE"));
            }
            body.extend_from_slice(&chunk);
        }
        self.check_foreground()?;
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
        self.capabilities().await.map(|_| ())
    }
    pub async fn capabilities(&self) -> Result<crate::sync_capabilities::Capabilities> {
        let result = self
            .json(Method::GET, "sync/v1/handshake?protocol=1", None)
            .await?;
        crate::sync_capabilities::parse(&result, self.endpoint.scheme() == "https")
            .map_err(Into::into)
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
        workspace.resume_resolutions(&binding.id).await?;
        workspace.capture(&binding.id).await?;
        let job = workspace.access(|ws| ws.sync_next(&binding.id))?;
        let Some(job) = job else {
            return Ok(false);
        };
        if job.state == "conflict" {
            return Err(SyncError::new("REVISION_CONFLICT"));
        }
        workspace.start_attempt(&job)?;
        let mut attempt = Attempt {
            workspace,
            job: &job,
            finished: false,
        };
        let mut transfer = Transfer::new(
            Direction::Upload,
            &job.path,
            job.size as u64,
            if job.operation == "put" && job.base_revision.is_none() {
                TransferPhase::Sending
            } else {
                TransferPhase::Committing
            },
        );
        if job.base_revision.is_some() {
            transfer.bytes_done = transfer.total_bytes;
        }
        self.report(Event::Transfer(transfer));
        let result = async {
            if job.operation == "put" && job.base_revision.is_none() {
                self.upload(workspace, binding, &job).await?;
            }
            let payload = workspace.commit_payload(&job).await?;
            let revision = self
                .json(
                    Method::POST,
                    &format!("sync/v1/vaults/{}/revisions", binding.remote_vault),
                    Some(payload),
                )
                .await?;
            workspace.acknowledge(&job, &revision).await?;
            self.report(Event::Processed(Direction::Upload));
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
        workspace.resume_resolutions(&binding.id).await?;
        while workspace.apply_pending(&binding.id).await? {}
        if let Some(initial) = workspace.access(|ws| ws.sync_initial_pending(&binding.id))? {
            let count = initial.len().min(100);
            for revision in initial.iter().take(count) {
                if revision.operation == "put"
                    && workspace.access(|ws| ws.sync_path_enabled(&revision.path))?
                {
                    self.download(workspace, binding, revision).await?;
                }
                workspace.stage_revision(&binding.id, revision).await?;
                workspace.apply_pending(&binding.id).await?;
                self.report(Event::Processed(Direction::Download));
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
            workspace.stage_revision(&binding.id, &revision).await?;
            workspace.apply_pending(&binding.id).await?;
            self.report(Event::Processed(Direction::Download));
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
        let mut transfer = Transfer::new(
            Direction::Download,
            &revision.path,
            revision.size as u64,
            TransferPhase::Receiving,
        );
        self.report(Event::Transfer(transfer.clone()));
        let expected = digest.to_owned();
        let size = revision.size as u64;
        let spool = self
            .io(move |check| {
                crate::sync_transfer_io::Download::prepare(target, expected, size, check)
            })
            .await?;
        workspace.access(|ws| ws.check_binding(&binding.id))?;
        let Some(mut spool) = spool else {
            transfer.bytes_done = transfer.total_bytes;
            transfer.phase = TransferPhase::Cached;
            self.report(Event::Transfer(transfer));
            return Ok(());
        };
        let url = self
            .endpoint
            .join(&format!(
                "sync/v1/vaults/{}/objects/{digest}",
                binding.remote_vault
            ))
            .map_err(|_| SyncError::new("SYNC_PATH_INVALID"))?;
        self.check_foreground()?;
        let mut response = self
            .guard_transport(async {
                self.client
                    .get(url)
                    .bearer_auth(self.token.as_str())
                    .send()
                    .await
                    .map_err(|_| SyncError::new("SYNC_NETWORK_ERROR"))
            })
            .await?;
        if !response.status().is_success() {
            return Err(SyncError {
                code: "SYNC_DOWNLOAD_FAILED".into(),
                status: response.status().as_u16(),
                retry_after: None,
            });
        }
        while let Some(chunk) = self
            .guard_transport(async {
                response
                    .chunk()
                    .await
                    .map_err(|_| SyncError::new("SYNC_NETWORK_ERROR"))
            })
            .await?
        {
            workspace.access(|ws| ws.check_binding(&binding.id))?;
            spool = self.io(move |check| spool.append(&chunk, check)).await?;
            transfer.bytes_done = spool.length();
            transfer.transferred_bytes = spool.length();
            self.report(Event::Transfer(transfer.clone()));
        }
        self.io(move |check| spool.finish(check)).await?;
        workspace.access(|ws| ws.check_binding(&binding.id))?;
        transfer.phase = TransferPhase::Verified;
        self.report(Event::Transfer(transfer));
        Ok(())
    }
    async fn upload(
        &self,
        workspace: &impl WorkspaceAccess,
        binding: &Binding,
        job: &Job,
    ) -> Result<()> {
        let path = workspace.access(|ws| ws.sync_spool(&job.hash))?;
        let expected = job.hash.clone();
        let size = job.size as u64;
        let mut file = self
            .io(move |check| crate::sync_transfer_io::Upload::open(path, &expected, size, check))
            .await?;
        workspace.access(|ws| ws.check_binding(&binding.id))?;
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
                let mut transfer = Transfer::new(
                    Direction::Upload,
                    &job.path,
                    job.size as u64,
                    TransferPhase::Cached,
                );
                transfer.bytes_done = transfer.total_bytes;
                self.report(Event::Transfer(transfer));
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
        let resumed = offset;
        let mut transfer = Transfer::new(
            Direction::Upload,
            &job.path,
            job.size as u64,
            TransferPhase::Sending,
        );
        transfer.bytes_done = resumed;
        transfer.resumed_bytes = resumed;
        self.report(Event::Transfer(transfer.clone()));
        while offset < job.size as u64 {
            workspace.access(|ws| ws.check_binding(&binding.id))?;
            let (original, buffer) = self.io(move |check| file.read_part(offset, check)).await?;
            file = original;
            let count = buffer.len();
            let value = self
                .send(
                    Method::PUT,
                    &format!("{base}/{id}?offset={offset}"),
                    Some(buffer),
                    false,
                )
                .await?;
            if value["offset"].as_u64() != Some(offset + count as u64) {
                return Err(SyncError::new("SYNC_RESPONSE_INVALID"));
            }
            offset += count as u64;
            transfer.bytes_done = offset;
            transfer.transferred_bytes = offset - resumed;
            self.report(Event::Transfer(transfer.clone()));
        }
        workspace.access(|ws| ws.check_binding(&binding.id))?;
        self.check_foreground()?;
        let value = self
            .json(Method::POST, &format!("{base}/{id}/complete"), None)
            .await?;
        if value["complete"] != true || value["content_hash"] != job.hash {
            return Err(SyncError::new("SYNC_RESPONSE_INVALID"));
        }
        transfer.phase = TransferPhase::Committing;
        self.report(Event::Transfer(transfer));
        Ok(())
    }
}
pub(crate) fn identifier(value: &str) -> Result<()> {
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
    #[test]
    fn production_endpoints_reject_plaintext_credentials_and_non_origin_urls() {
        for endpoint in [
            "http://sync.example/",
            "ftp://sync.example/",
            "https://user:password@sync.example/",
            "https://sync.example/?token=secret",
            "https://sync.example/#fragment",
            "https://sync.example/other/path",
        ] {
            assert!(matches!(
                SyncClient::new(endpoint, Zeroizing::new(String::new()), false),
                Err(error) if error.code == "SYNC_ENDPOINT_INVALID"
            ));
        }
    }

    #[tokio::test]
    async fn owned_http_probe_keeps_tokens_in_headers_and_does_not_follow_redirects() {
        use std::io::Write;
        let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let address = listener.local_addr().unwrap();
        let redirected = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        redirected.set_nonblocking(true).unwrap();
        let redirect_address = redirected.local_addr().unwrap();
        let server = std::thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            stream
                .set_read_timeout(Some(Duration::from_secs(5)))
                .unwrap();
            let mut request = Vec::new();
            while !request.ends_with(b"\r\n\r\n") {
                assert!(request.len() < 64 * 1024);
                let mut byte = [0];
                assert_eq!(stream.read(&mut byte).unwrap(), 1);
                request.push(byte[0]);
            }
            let body = br#"{"error":{"code":"SYNC_REDIRECT_BLOCKED"}}"#;
            write!(stream,"HTTP/1.1 302 Found\r\nContent-Type: application/json\r\nContent-Length: {}\r\nLocation: http://{}/not-followed\r\nConnection: close\r\n\r\n",body.len(),redirect_address).unwrap();
            stream.write_all(body).unwrap();
            String::from_utf8(request).unwrap()
        });
        let client = SyncClient::new(
            &format!("http://{address}"),
            Zeroizing::new("test-only-header-token".into()),
            true,
        )
        .unwrap();
        let error = client.handshake().await.err().unwrap();
        assert_eq!(error.status, 302);
        assert_eq!(error.code, "SYNC_REDIRECT_BLOCKED");
        let request = server.join().unwrap();
        assert_eq!(
            request.lines().next(),
            Some("GET /sync/v1/handshake?protocol=1 HTTP/1.1")
        );
        assert!(request
            .to_ascii_lowercase()
            .contains("authorization: bearer test-only-header-token\r\n"));
        assert!(!request.lines().next().unwrap().contains("token"));
        assert!(
            matches!(redirected.accept(), Err(error) if error.kind() == std::io::ErrorKind::WouldBlock)
        );
    }

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
