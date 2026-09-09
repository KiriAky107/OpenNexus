//! 原生实例工作线程。必须在 UI 线程之外创建和销毁此管理器；
//! 生产调用方仍须满足完整的启动策略约定。
use crate::{
    credentials::CredentialBroker,
    extension_call_authorization::{Identity, Review},
    extension_container::Profile,
    extension_launch_authorization::Context,
    extension_mcp::Session,
    extension_mcp_tools::Description,
    extension_package::Inventory,
    extension_permit::{Authority, Claims, Permit},
    extension_pinned::PinnedPackage,
    workspace::{HostError, Result},
};
use serde::Serialize;
use serde_json::Value;
use sha2::{Digest, Sha256};
use std::{
    collections::BTreeMap,
    path::PathBuf,
    sync::{
        atomic::{AtomicBool, AtomicU8, Ordering},
        mpsc::{self, Receiver, RecvTimeoutError, SyncSender},
        Arc, Mutex,
    },
    thread::JoinHandle,
    time::{Duration, SystemTime, UNIX_EPOCH},
};
fn now_ms() -> Result<u64> {
    u64::try_from(
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map_err(|_| HostError::new("EXTENSION_CLOCK_INVALID"))?
            .as_millis(),
    )
    .map_err(|_| HostError::new("EXTENSION_CLOCK_INVALID"))
}
pub type ResumeCheck = Box<dyn FnOnce(&Claims) -> Result<()> + Send>;
pub struct LaunchSpec {
    pub package: cap_std::fs::Dir,
    pub inventory: Inventory,
    pub claims: Claims,
    pub permit: Permit,
    pub authority: Arc<Authority>,
    pub credentials: Arc<Mutex<CredentialBroker>>,
    pub vault_id: String,
    pub policy_version: String,
    pub system_root: PathBuf,
    /// 在昂贵的软件包检查之后，在恢复之前立即重新验证活动安装/当前信任和所有外部策略。错误禁止执行。
    pub before_resume: ResumeCheck,
}
#[derive(Clone, Copy, Serialize, PartialEq, Eq, Debug)]
#[serde(rename_all = "snake_case")]
pub enum Status {
    Starting,
    Ready,
    Stopping,
    Stopped,
    Failed,
}
#[derive(Serialize)]
pub struct Snapshot {
    pub status: Status,
    pub identity: Option<Identity>,
    pub tool_count: usize,
    pub error: Option<String>,
}
struct Control {
    stop: AtomicBool,
    #[cfg(test)]
    job: Mutex<Option<crate::extension_job::Job>>,
    active: Mutex<Option<Arc<AtomicBool>>>,
    status: AtomicU8,
    identity: Mutex<Option<Identity>>,
    tools: Mutex<Vec<Description>>,
    error: Mutex<Option<String>>,
}
impl Control {
    fn status(&self) -> Status {
        match self.status.load(Ordering::Acquire) {
            0 => Status::Starting,
            1 => Status::Ready,
            2 => Status::Stopping,
            3 => Status::Stopped,
            _ => Status::Failed,
        }
    }
    fn stop(&self) {
        self.stop.store(true, Ordering::Release);
        if let Some(active) = &*self.active.lock().unwrap_or_else(|e| e.into_inner()) {
            active.store(true, Ordering::Release);
        }
        let _ = self
            .status
            .fetch_update(Ordering::AcqRel, Ordering::Acquire, |value| {
                (value < 2).then_some(2)
            });
    }
}
struct Active<'a>(&'a Control);
impl Drop for Active<'_> {
    fn drop(&mut self) {
        self.0
            .active
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .take();
    }
}
struct Request<T> {
    cancel: Arc<AtomicBool>,
    reply: SyncSender<Result<T>>,
}
impl<T> Request<T> {
    fn execute(
        self,
        control: &Control,
        action: impl FnOnce(&AtomicBool) -> Result<T>,
    ) -> Option<String> {
        let mut active = control.active.lock().unwrap_or_else(|e| e.into_inner());
        if control.stop.load(Ordering::Acquire) || self.cancel.load(Ordering::Acquire) {
            let _ = self
                .reply
                .try_send(Err(HostError::new("EXTENSION_INSTANCE_CANCELLED")));
            return None;
        }
        *active = Some(Arc::clone(&self.cancel));
        drop(active);
        let _active = Active(control);
        let result = action(&self.cancel);
        let error = result.as_ref().err().map(|e| e.code.clone());
        let _ = self.reply.try_send(result);
        error
    }
}
enum Command {
    Review {
        name: String,
        arguments: Value,
        request: Request<Review>,
    },
    Invoke {
        review_id: String,
        request: Request<Value>,
    },
    Refresh {
        request: Request<Vec<Description>>,
    },
}
pub struct Ticket<T> {
    receiver: Receiver<Result<T>>,
    cancel: Arc<AtomicBool>,
}
impl<T> Ticket<T> {
    /// Background wait only; dropping a ticket cancels its queued/in-flight work.
    pub fn wait(self, timeout: Duration) -> Result<T> {
        if timeout > Duration::from_secs(65) {
            return Err(HostError::new("EXTENSION_INSTANCE_WAIT_INVALID"));
        }
        self.receiver.recv_timeout(timeout).map_err(|error| {
            HostError::new(match error {
                RecvTimeoutError::Timeout => "EXTENSION_INSTANCE_TIMEOUT",
                RecvTimeoutError::Disconnected => "EXTENSION_INSTANCE_CLOSED",
            })
        })?
    }
    pub fn cancel(&self) {
        self.cancel.store(true, Ordering::Release);
    }
}
impl<T> Drop for Ticket<T> {
    fn drop(&mut self) {
        self.cancel.store(true, Ordering::Release);
    }
}
#[derive(Clone)]
pub struct Endpoint {
    control: Arc<Control>,
    commands: SyncSender<Command>,
}
impl Endpoint {
    fn submit<T>(&self, build: impl FnOnce(Request<T>) -> Command) -> Result<Ticket<T>> {
        if self.control.status() != Status::Ready || self.control.stop.load(Ordering::Acquire) {
            return Err(HostError::new("EXTENSION_INSTANCE_NOT_READY"));
        }
        let (reply, receiver) = mpsc::sync_channel(1);
        let cancel = Arc::new(AtomicBool::new(false));
        self.commands
            .try_send(build(Request {
                cancel: Arc::clone(&cancel),
                reply,
            }))
            .map_err(|_| HostError::new("EXTENSION_INSTANCE_BACKPRESSURE"))?;
        Ok(Ticket { receiver, cancel })
    }
    pub fn review(&self, name: String, arguments: Value) -> Result<Ticket<Review>> {
        if name.is_empty() || name.len() > 128 {
            return Err(HostError::new("EXTENSION_MCP_ARGUMENTS_INVALID"));
        }
        crate::extension_mcp_tools::argument_bounds(&arguments)?;
        self.submit(|request| Command::Review {
            name,
            arguments,
            request,
        })
    }
    /// Host route only: the user must have approved the exact saved review.
    /// Confirmation and consumption happen together on the instance thread.
    pub fn invoke_confirmed(&self, review_id: String) -> Result<Ticket<Value>> {
        if uuid::Uuid::parse_str(&review_id).is_err() || review_id.len() != 36 {
            return Err(HostError::new("EXTENSION_CALL_REVIEW_UNKNOWN"));
        }
        self.submit(|request| Command::Invoke { review_id, request })
    }
    pub fn refresh(&self) -> Result<Ticket<Vec<Description>>> {
        self.submit(|request| Command::Refresh { request })
    }
    pub fn stop(&self) {
        self.control.stop();
    }
    pub fn snapshot(&self) -> Snapshot {
        Snapshot {
            status: self.control.status(),
            identity: self
                .control
                .identity
                .lock()
                .unwrap_or_else(|e| e.into_inner())
                .clone(),
            tool_count: self
                .control
                .tools
                .lock()
                .unwrap_or_else(|e| e.into_inner())
                .len(),
            error: self
                .control
                .error
                .lock()
                .unwrap_or_else(|e| e.into_inner())
                .clone(),
        }
    }
}
struct Entry {
    endpoint: Endpoint,
    worker: JoinHandle<()>,
}
#[derive(Default)]
pub struct Registry {
    entries: BTreeMap<String, Entry>,
}
impl Registry {
    /// # Safety
    /// The caller must establish all sandbox limits and current install/user
    /// authorization. before_resume must recheck live trust/active installation.
    /// This API is not exposed to renderer/Core and does not enable extensions.
    pub unsafe fn start(&mut self, spec: LaunchSpec) -> Result<Endpoint> {
        self.reap();
        spec.authority
            .verify(&spec.permit, &spec.claims, now_ms()?)?;
        let key = format!(
            "{:x}",
            Sha256::digest(
                serde_json::to_vec(&(
                    spec.claims.kind,
                    &spec.claims.source,
                    &spec.claims.namespace,
                    &spec.claims.package_id,
                    &spec.claims.vault_id
                ))
                .map_err(|_| HostError::new("EXTENSION_INSTANCE_INVALID"))?
            )
        );
        if self.entries.contains_key(&key) {
            return Err(HostError::new("EXTENSION_INSTANCE_ALREADY_RUNNING"));
        }
        if self.entries.len() >= 16 {
            return Err(HostError::new("EXTENSION_INSTANCE_LIMIT"));
        }
        let control = Arc::new(Control {
            stop: AtomicBool::new(false),
            #[cfg(test)]
            job: Mutex::new(None),
            active: Mutex::new(None),
            status: AtomicU8::new(0),
            identity: Mutex::new(None),
            tools: Mutex::new(Vec::new()),
            error: Mutex::new(None),
        });
        let (commands, receiver) = mpsc::sync_channel(4);
        let endpoint = Endpoint {
            control: Arc::clone(&control),
            commands,
        };
        let worker = std::thread::Builder::new()
            .name("opennexus-instance".into())
            .spawn(move || {
                let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                    run(spec, &control, receiver)
                }));
                // All native stack owners have dropped before publishing terminal state.
                let error = match result {
                    Ok(Ok(())) => None,
                    Ok(Err(error))
                        if control.stop.load(Ordering::Acquire)
                            && matches!(
                                error.code.as_str(),
                                "EXTENSION_MCP_CANCELLED" | "EXTENSION_INSTANCE_CANCELLED"
                            ) =>
                    {
                        None
                    }
                    Ok(Err(error)) => Some(error.code),
                    Err(_) => Some("EXTENSION_INSTANCE_WORKER_FAILED".into()),
                };
                let failed = error.is_some();
                *control.error.lock().unwrap_or_else(|e| e.into_inner()) = error;
                control
                    .tools
                    .lock()
                    .unwrap_or_else(|e| e.into_inner())
                    .clear();
                control
                    .status
                    .store(if failed { 4 } else { 3 }, Ordering::Release);
            })
            .map_err(|_| HostError::new("EXTENSION_INSTANCE_WORKER_UNAVAILABLE"))?;
        self.entries.insert(
            key,
            Entry {
                endpoint: endpoint.clone(),
                worker,
            },
        );
        Ok(endpoint)
    }
    /// Reap only threads confirmed finished, so an old generation cannot overlap
    /// a replacement merely because stop was requested or status was changed.
    pub fn reap(&mut self) {
        let done: Vec<_> = self
            .entries
            .iter()
            .filter(|(_, entry)| {
                entry.worker.is_finished()
                    && !entry
                        .endpoint
                        .control
                        .error
                        .lock()
                        .unwrap_or_else(|e| e.into_inner())
                        .as_deref()
                        .is_some_and(|code| {
                            matches!(
                                code,
                                "EXTENSION_CONTAINER_CLEANUP_FAILED"
                                    | "EXTENSION_CONTAINER_ACL_REVOKE_FAILED"
                                    | "EXTENSION_RESOURCE_TERMINATE_FAILED"
                                    | "EXTENSION_INSTANCE_WORKER_FAILED"
                            )
                        })
            })
            .map(|(key, _)| key.clone())
            .collect();
        for key in done {
            if let Some(entry) = self.entries.remove(&key) {
                let _ = entry.worker.join();
            }
        }
    }
    pub fn stop_all(&self) {
        for entry in self.entries.values() {
            entry.endpoint.stop();
        }
    }
}
impl Drop for Registry {
    fn drop(&mut self) {
        self.stop_all();
        for (_, entry) in std::mem::take(&mut self.entries) {
            let _ = entry.worker.join();
        }
    }
}
fn run(spec: LaunchSpec, control: &Control, receiver: Receiver<Command>) -> Result<()> {
    if control.stop.load(Ordering::Acquire) {
        return Ok(());
    }
    let profile = Profile::create()?;
    let result = run_in_profile(spec, control, receiver, &profile);
    profile.remove()?;
    result
}
fn run_in_profile(
    spec: LaunchSpec,
    control: &Control,
    receiver: Receiver<Command>,
    profile: &Profile,
) -> Result<()> {
    let pinned = PinnedPackage::open(&spec.package, &spec.inventory, &spec.claims.tree_sha256)?;
    let access = pinned.access(profile)?;
    let result = run_with_access(spec, control, receiver, profile, &pinned);
    access.finish()?;
    result
}
fn run_with_access(
    spec: LaunchSpec,
    control: &Control,
    receiver: Receiver<Command>,
    profile: &Profile,
    pinned: &PinnedPackage,
) -> Result<()> {
    let entry = pinned.bind_entry(&spec.claims.entry)?;
    let folder = profile.folder()?;
    let scratch = folder.join("Temp");
    let context = Context {
        vault_id: &spec.vault_id,
        policy_version: &spec.policy_version,
        system_root: &spec.system_root,
        container_data: &folder,
        scratch: &scratch,
    };
    let prepared = {
        let credentials = spec
            .credentials
            .lock()
            .map_err(|_| HostError::new("CREDENTIALS_LOCKED"))?;
        context.prepare(
            &spec.authority,
            &spec.permit,
            &spec.claims,
            &entry,
            &credentials,
            now_ms()?,
        )?
    };
    let (suspended, io) = prepared.create_suspended_with_stdio(profile, &entry)?;
    (spec.before_resume)(&spec.claims)?;
    if control.stop.load(Ordering::Acquire) {
        return Ok(());
    }
    // Safety obligation belongs to Registry::start's caller, rechecked above.
    let running = unsafe { suspended.resume()? };
    #[cfg(test)]
    {
        *control.job.lock().unwrap() = Some(running.test_job()?);
    }
    let mut session = Session::new(&running, io)?;
    session.initialize(&control.stop)?;
    let tools = session.refresh_tools(&control.stop)?;
    *control.identity.lock().unwrap_or_else(|e| e.into_inner()) = Some(running.call_identity()?);
    *control.tools.lock().unwrap_or_else(|e| e.into_inner()) = tools;
    if control.stop.load(Ordering::Acquire) {
        return session.shutdown();
    }
    let _ = control
        .status
        .compare_exchange(0, 1, Ordering::AcqRel, Ordering::Acquire);
    while !control.stop.load(Ordering::Acquire) {
        session.drain_pending()?;
        if session.take_tools_changed() {
            control
                .tools
                .lock()
                .unwrap_or_else(|e| e.into_inner())
                .clear();
        }
        let error = match receiver.recv_timeout(Duration::from_millis(20)) {
            Ok(Command::Review {
                name,
                arguments,
                request,
            }) => request.execute(control, |_| session.review_call(&name, arguments)),
            Ok(Command::Invoke { review_id, request }) => request.execute(control, |cancel| {
                let approved = session.confirm_call(&review_id)?;
                session.call_tool(approved, cancel)
            }),
            Ok(Command::Refresh { request }) => request.execute(control, |cancel| {
                let tools = session.refresh_tools(cancel)?;
                *control.tools.lock().unwrap_or_else(|e| e.into_inner()) = tools.clone();
                Ok(tools)
            }),
            Err(RecvTimeoutError::Timeout) => None,
            Err(RecvTimeoutError::Disconnected) => break,
        };
        if session.is_failed() {
            if error.as_deref() == Some("EXTENSION_MCP_CANCELLED") {
                return session.shutdown();
            }
            return Err(HostError::new(
                error.as_deref().unwrap_or("EXTENSION_INSTANCE_FAILED"),
            ));
        }
    }
    session.shutdown()
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    use std::time::Instant;
    use zeroize::Zeroizing;
    fn wait_for(mut condition: impl FnMut() -> bool) {
        let started = Instant::now();
        while !condition() {
            assert!(started.elapsed() < Duration::from_secs(10));
            std::thread::sleep(Duration::from_millis(5));
        }
    }
    #[test]
    fn native_worker_routes_reviews_cancels_calls_and_reaps_generations() {
        native_worker_lifecycle(false);
    }
    #[test]
    #[ignore = "real background MCP CPU/memory/process exhaustion and restart; run explicitly"]
    fn native_resource_failures_are_reaped_and_replacements_can_start() {
        native_worker_lifecycle(true);
    }
    fn native_worker_lifecycle(resources: bool) {
        let temp = tempfile::tempdir().unwrap();
        let package = temp.path().join("package");
        std::fs::create_dir(&package).unwrap();
        let executable = package.join("entry.exe");
        let fixture = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("tests/fixtures/sandbox_network_probe.rs");
        let compiled = std::process::Command::new("rustc")
            .arg("--edition=2021")
            .arg(fixture)
            .arg("-o")
            .arg(&executable)
            .output()
            .unwrap();
        assert!(
            compiled.status.success(),
            "{}",
            String::from_utf8_lossy(&compiled.stderr)
        );
        let bytes = std::fs::read(&executable).unwrap();
        let files: BTreeMap<String, String> =
            [("entry.exe".into(), format!("{:x}", Sha256::digest(&bytes)))]
                .into_iter()
                .collect();
        let dir =
            cap_std::fs::Dir::open_ambient_dir(&package, cap_std::ambient_authority()).unwrap();
        let acl_file = std::fs::File::open(&executable).unwrap();
        let acl_root = dir.try_clone().unwrap().into_std_file();
        let file_acl = crate::extension_container::test_acl_entries(&acl_file);
        let root_acl = crate::extension_container::test_acl_entries(&acl_root);
        let inventory = || Inventory {
            files: files.clone(),
            expanded_size: bytes.len() as u64,
            manifest: "entry.exe".into(),
        };
        let tree = crate::extension_unpack::verify_tree(&dir, &inventory()).unwrap();
        let authority = Arc::new(Authority::default());
        let credentials = Arc::new(Mutex::new(CredentialBroker::new(
            temp.path().join("credentials.v1"),
        )));
        credentials
            .lock()
            .unwrap()
            .unlock(Zeroizing::new(b"instance fixture password".to_vec()))
            .unwrap();
        let vault_id = uuid::Uuid::new_v4().to_string();
        let make = |mode: &str| {
            let claims = Claims {
                kind: crate::extension_permit::ExecutionKind::Mcp,
                source: "https://catalog.example/".into(),
                namespace: "examples".into(),
                package_id: "native-worker".into(),
                version: "1.0.0".into(),
                archive_sha256: "a".repeat(64),
                tree_sha256: tree.clone(),
                signer_sha256: "b".repeat(64),
                entry: "entry.exe".into(),
                arguments: vec![mode.into()],
                environment: BTreeMap::new(),
                permissions: Default::default(),
                vault_id: vault_id.clone(),
                platform: "windows".into(),
                policy_version: "1".into(),
                expires_at_ms: now_ms().unwrap() + 120_000,
            };
            LaunchSpec {
                package: dir.try_clone().unwrap(),
                inventory: inventory(),
                permit: authority.issue(&claims, now_ms().unwrap()).unwrap(),
                claims,
                authority: Arc::clone(&authority),
                credentials: Arc::clone(&credentials),
                vault_id: vault_id.clone(),
                policy_version: "1".into(),
                system_root: PathBuf::from(std::env::var_os("SystemRoot").unwrap()),
                before_resume: Box::new(|_| Ok(())),
            }
        };
        let mut registry = Registry::default();
        let mut invalid = make("mcp");
        invalid.claims.arguments = vec!["changed".into()];
        assert!(unsafe { registry.start(invalid) }.is_err());
        assert!(registry.entries.is_empty());
        let endpoint = unsafe { registry.start(make("mcp")) }.unwrap();
        assert_eq!(
            unsafe { registry.start(make("mcp")) }.err().unwrap().code,
            "EXTENSION_INSTANCE_ALREADY_RUNNING"
        );
        wait_for(|| endpoint.snapshot().status != Status::Starting);
        assert_eq!(
            endpoint.snapshot().status,
            Status::Ready,
            "{:?}",
            endpoint.snapshot().error
        );
        assert_eq!(endpoint.snapshot().tool_count, 1);
        let first_identity = serde_json::to_value(endpoint.snapshot().identity.unwrap()).unwrap();
        let review = endpoint
            .review("echo".into(), json!({}))
            .unwrap()
            .wait(Duration::from_secs(5))
            .unwrap();
        let result = endpoint
            .invoke_confirmed(review.review_id.clone())
            .unwrap()
            .wait(Duration::from_secs(5))
            .unwrap();
        assert_eq!(result["content"][0]["text"], "native MCP success");
        assert_eq!(
            endpoint
                .invoke_confirmed(review.review_id)
                .unwrap()
                .wait(Duration::from_secs(5))
                .unwrap_err()
                .code,
            "EXTENSION_CALL_REVIEW_UNKNOWN"
        );
        endpoint.stop();
        wait_for(|| {
            registry.reap();
            registry.entries.is_empty()
        });
        assert_eq!(
            endpoint.snapshot().status,
            Status::Stopped,
            "{:?}",
            endpoint.snapshot().error
        );
        assert!(endpoint.review("echo".into(), json!({})).is_err());
        assert_eq!(
            endpoint
                .control
                .job
                .lock()
                .unwrap()
                .as_ref()
                .unwrap()
                .active_processes()
                .unwrap(),
            0
        );
        let second = unsafe { registry.start(make("mcp_cancel")) }.unwrap();
        wait_for(|| second.snapshot().status != Status::Starting);
        assert_eq!(
            second.snapshot().status,
            Status::Ready,
            "{:?}",
            second.snapshot().error
        );
        assert_ne!(
            serde_json::to_value(second.snapshot().identity.unwrap()).unwrap()["instance_id"],
            first_identity["instance_id"]
        );
        let review = second
            .review("echo".into(), json!({}))
            .unwrap()
            .wait(Duration::from_secs(5))
            .unwrap();
        let baseline = second
            .control
            .job
            .lock()
            .unwrap()
            .as_ref()
            .unwrap()
            .active_processes()
            .unwrap();
        let ticket = second.invoke_confirmed(review.review_id).unwrap();
        wait_for(|| {
            let active = second
                .control
                .active
                .lock()
                .unwrap()
                .as_ref()
                .is_some_and(|active| Arc::ptr_eq(active, &ticket.cancel));
            let processes = second
                .control
                .job
                .lock()
                .unwrap()
                .as_ref()
                .unwrap()
                .active_processes()
                .unwrap();
            active && processes > baseline
        });
        ticket.cancel();
        assert_eq!(
            ticket.wait(Duration::from_secs(5)).unwrap_err().code,
            "EXTENSION_MCP_CANCELLED"
        );
        wait_for(|| {
            registry.reap();
            registry.entries.is_empty()
        });
        assert_eq!(
            second.snapshot().status,
            Status::Stopped,
            "{:?}",
            second.snapshot().error
        );
        assert_eq!(
            second
                .control
                .job
                .lock()
                .unwrap()
                .as_ref()
                .unwrap()
                .active_processes()
                .unwrap(),
            0
        );
        for (mode, expected) in if resources {
            vec![
                ("mcp_cpu", "EXTENSION_RESOURCE_CPU_EXCEEDED"),
                ("mcp_memory", "EXTENSION_RESOURCE_MEMORY_EXCEEDED"),
                ("mcp_processes", "EXTENSION_RESOURCE_PROCESSES_EXCEEDED"),
            ]
        } else {
            Vec::new()
        } {
            let exhausted = unsafe { registry.start(make(mode)) }.unwrap();
            wait_for(|| exhausted.snapshot().status != Status::Starting);
            assert_eq!(exhausted.snapshot().status, Status::Ready);
            let old_identity =
                serde_json::to_value(exhausted.snapshot().identity.unwrap()).unwrap();
            let review = exhausted
                .review("echo".into(), json!({}))
                .unwrap()
                .wait(Duration::from_secs(5))
                .unwrap();
            let started = Instant::now();
            assert_eq!(
                exhausted
                    .invoke_confirmed(review.review_id)
                    .unwrap()
                    .wait(Duration::from_secs(if mode == "mcp_cpu" { 20 } else { 10 }))
                    .unwrap_err()
                    .code,
                expected
            );
            eprintln!("background {mode} call error after {:?}", started.elapsed());
            wait_for(|| {
                registry.reap();
                registry.entries.is_empty()
            });
            assert_eq!(exhausted.snapshot().status, Status::Failed);
            assert_eq!(exhausted.snapshot().error.as_deref(), Some(expected));
            assert_eq!(exhausted.snapshot().tool_count, 0);
            assert!(exhausted.review("echo".into(), json!({})).is_err());
            assert_eq!(
                exhausted
                    .control
                    .job
                    .lock()
                    .unwrap()
                    .as_ref()
                    .unwrap()
                    .active_processes()
                    .unwrap(),
                0
            );
            assert_eq!(
                crate::extension_container::test_acl_entries(&acl_file),
                file_acl
            );
            assert_eq!(
                crate::extension_container::test_acl_entries(&acl_root),
                root_acl
            );
            let replacement = unsafe { registry.start(make("mcp")) }.unwrap();
            wait_for(|| replacement.snapshot().status != Status::Starting);
            assert_eq!(replacement.snapshot().status, Status::Ready);
            assert_ne!(
                serde_json::to_value(replacement.snapshot().identity.unwrap()).unwrap()
                    ["instance_id"],
                old_identity["instance_id"]
            );
            let review = replacement
                .review("echo".into(), json!({}))
                .unwrap()
                .wait(Duration::from_secs(5))
                .unwrap();
            assert_eq!(
                replacement
                    .invoke_confirmed(review.review_id)
                    .unwrap()
                    .wait(Duration::from_secs(5))
                    .unwrap()["content"][0]["text"],
                "native MCP success"
            );
            replacement.stop();
            wait_for(|| {
                registry.reap();
                registry.entries.is_empty()
            });
            assert_eq!(replacement.snapshot().status, Status::Stopped);
        }
        let called = Arc::new(AtomicBool::new(false));
        let marker = Arc::clone(&called);
        let mut denied = make("mcp");
        denied.before_resume = Box::new(move |_| {
            marker.store(true, Ordering::Release);
            Err(HostError::new("FIXTURE_POLICY_DENIED"))
        });
        let denied = unsafe { registry.start(denied) }.unwrap();
        wait_for(|| {
            registry.reap();
            registry.entries.is_empty()
        });
        assert!(called.load(Ordering::Acquire));
        assert_eq!(denied.snapshot().status, Status::Failed);
        assert_eq!(
            denied.snapshot().error.as_deref(),
            Some("FIXTURE_POLICY_DENIED")
        );
        assert!(denied.control.job.lock().unwrap().is_none());
        let locked = unsafe { registry.start(make("mcp")) }.unwrap();
        wait_for(|| locked.snapshot().status != Status::Starting);
        assert_eq!(locked.snapshot().status, Status::Ready);
        credentials.lock().unwrap().lock();
        wait_for(|| {
            registry.reap();
            registry.entries.is_empty()
        });
        assert_eq!(locked.snapshot().status, Status::Failed);
        assert_eq!(
            locked.snapshot().error.as_deref(),
            Some("CREDENTIALS_LOCKED")
        );
        assert_eq!(
            locked
                .control
                .job
                .lock()
                .unwrap()
                .as_ref()
                .unwrap()
                .active_processes()
                .unwrap(),
            0
        );
        assert_eq!(
            crate::extension_container::test_acl_entries(&acl_file),
            file_acl
        );
        assert_eq!(
            crate::extension_container::test_acl_entries(&acl_root),
            root_acl
        );
    }
    fn channel() -> (Endpoint, Receiver<Command>) {
        let (commands, receiver) = mpsc::sync_channel(4);
        let control = Arc::new(Control {
            stop: AtomicBool::new(false),
            job: Mutex::new(None),
            active: Mutex::new(None),
            status: AtomicU8::new(1),
            identity: Mutex::new(None),
            tools: Mutex::new(Vec::new()),
            error: Mutex::new(None),
        });
        (Endpoint { control, commands }, receiver)
    }
    #[test]
    fn bounded_queue_and_stop_prevent_queued_work_from_executing() {
        let (endpoint, receiver) = channel();
        let mut tickets = Vec::new();
        for _ in 0..4 {
            tickets.push(endpoint.review("echo".into(), json!({})).unwrap());
        }
        assert_eq!(
            endpoint
                .review("echo".into(), json!({}))
                .err()
                .unwrap()
                .code,
            "EXTENSION_INSTANCE_BACKPRESSURE"
        );
        assert!(endpoint
            .review("echo".into(), json!({"large":"x".repeat(256 * 1024)}))
            .is_err());
        endpoint.stop();
        assert_eq!(endpoint.snapshot().status, Status::Stopping);
        for ticket in tickets {
            let Command::Review { request, .. } = receiver.try_recv().unwrap() else {
                panic!("wrong request")
            };
            request.execute(&endpoint.control, |_| panic!("stopped queue executed"));
            assert_eq!(
                ticket.wait(Duration::from_secs(1)).err().unwrap().code,
                "EXTENSION_INSTANCE_CANCELLED"
            );
        }
        assert!(endpoint.refresh().is_err());
    }
    #[test]
    fn cleanup_failures_remain_quarantined_even_after_worker_exit() {
        let (endpoint, _receiver) = channel();
        endpoint.control.status.store(4, Ordering::Release);
        *endpoint.control.error.lock().unwrap() = Some("EXTENSION_CONTAINER_CLEANUP_FAILED".into());
        let worker = std::thread::spawn(|| {});
        wait_for(|| worker.is_finished());
        let mut registry = Registry::default();
        registry
            .entries
            .insert("quarantined".into(), Entry { endpoint, worker });
        registry.reap();
        assert_eq!(registry.entries.len(), 1);
    }
}
