//! The same bounded incoming-window-before-outgoing order for native hosts.
//!
//! The caller supplies its authenticated client and identity/foreground-scoped
//! workspace. This coordinator never owns credentials, restarts an operation or
//! replaces an outbox ID. An unfinished frozen window is resumed on a later run.
use crate::{
    sync_client::{SyncClient, SyncError, WorkspaceAccess},
    sync_progress::{Event, Phase},
    sync_state::Binding,
};
use std::sync::Arc;

pub async fn run(
    client: SyncClient,
    workspace: &impl WorkspaceAccess,
    binding: &Binding,
    report: Arc<dyn Fn(Event) + Send + Sync>,
) -> Result<(), SyncError> {
    let client = client.with_progress(report.clone());
    client.handshake().await?;
    report(Event::Phase(Phase::Discover));
    workspace.discover(&binding.id).await?;
    report(Event::Phase(Phase::Pull));
    for _ in 0..10 {
        if client.pull_page(workspace, binding).await? == 0 {
            break;
        }
    }
    // Never freeze a new remote base while an original incoming window remains.
    if workspace
        .access(|ws| ws.sync_boundary(&binding.id))?
        .is_some()
    {
        return Ok(());
    }
    report(Event::Phase(Phase::Push));
    for _ in 0..20 {
        if !client.push_one(workspace, binding).await? {
            break;
        }
    }
    report(Event::Phase(Phase::Pull));
    client.pull_page(workspace, binding).await?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::workspace::Workspace;
    use serde_json::{json, Value};
    use std::{
        io::{Read, Write},
        net::TcpListener,
        sync::Mutex,
        time::{Duration, Instant},
    };
    use zeroize::Zeroizing;

    fn origin(responses: Vec<Value>) -> (String, std::thread::JoinHandle<Vec<String>>) {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let endpoint = format!("http://{}", listener.local_addr().unwrap());
        listener.set_nonblocking(true).unwrap();
        let thread = std::thread::spawn(move || {
            let mut requests = Vec::new();
            for body in responses {
                let deadline = Instant::now() + Duration::from_secs(8);
                let mut stream = loop {
                    match listener.accept() {
                        Ok((stream, _)) => break stream,
                        Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => {
                            assert!(
                                Instant::now() < deadline,
                                "Original owned request not observed"
                            );
                            std::thread::sleep(Duration::from_millis(5));
                        }
                        Err(error) => panic!("Owned loopback origin failed: {error}"),
                    }
                };
                stream
                    .set_read_timeout(Some(Duration::from_secs(5)))
                    .unwrap();
                let mut request = Vec::new();
                while !request.ends_with(b"\r\n\r\n") {
                    assert!(request.len() < 65536);
                    let mut byte = [0];
                    assert_eq!(stream.read(&mut byte).unwrap(), 1);
                    request.push(byte[0]);
                }
                requests.push(String::from_utf8(request).unwrap());
                let data = serde_json::to_vec(&body).unwrap();
                write!(stream,"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",data.len()).unwrap();
                stream.write_all(&data).unwrap();
            }
            requests
        });
        (endpoint, thread)
    }
    fn handshake() -> Value {
        serde_json::from_str::<Value>(include_str!("../tests/fixtures/sync-v1-contract.json"))
            .unwrap()["handshake"]
            .clone()
    }

    #[tokio::test]
    async fn incomplete_original_window_never_pushes_or_replaces_the_pending_local_operation() {
        let mut responses = vec![handshake()];
        for sequence in 1..=10 {
            responses.push(json!({"boundary":11,"items":[{
                "vault_id":"remote","sequence":sequence,"file_id":format!("remote-file-{sequence:06}"),
                "base_revision":0,"path":format!("remote-{sequence}.md"),"operation":"delete",
                "hash":null,"size":0,"operation_id":uuid::Uuid::new_v4().to_string()
            }]}));
        }
        let (endpoint, server) = origin(responses);
        let root = tempfile::tempdir().unwrap();
        let mut ws = Workspace::open(root.path()).unwrap();
        ws.write("original.md", "", b"local original\r\n", "local")
            .unwrap();
        let binding = ws.sync_bind_empty(&endpoint, "remote", "account").unwrap();
        let original = ws.sync_next(&binding.id).unwrap().unwrap();
        let workspace = Arc::new(Mutex::new(ws));
        let phases = Arc::new(Mutex::new(Vec::new()));
        let observed = phases.clone();
        run(
            SyncClient::new(&endpoint, Zeroizing::new("owned-test-only".into()), true).unwrap(),
            &workspace,
            &binding,
            Arc::new(move |event| {
                if let Event::Phase(phase) = event {
                    observed.lock().unwrap().push(phase);
                }
            }),
        )
        .await
        .unwrap();
        let requests = server.join().unwrap();
        assert_eq!(requests.len(), 11);
        assert!(requests.iter().all(|request| request.starts_with("GET ")));
        assert!(requests
            .last()
            .unwrap()
            .starts_with("GET /sync/v1/vaults/remote/changes?cursor=9&limit=100&boundary=11 "));
        assert!(!phases.lock().unwrap().contains(&Phase::Push));
        workspace
            .access(|ws| {
                assert_eq!(ws.sync_binding()?.unwrap().cursor, 10);
                assert_eq!(ws.sync_boundary(&binding.id)?, Some(11));
                let pending = ws.sync_next(&binding.id)?.unwrap();
                assert_eq!(pending.operation_id, original.operation_id);
                assert_eq!(pending.base_revision, original.base_revision);
                assert!(ws.sync_attempts(&binding.id)?.is_empty());
                assert_eq!(
                    ws.read("original.md")?.content.as_bytes(),
                    b"local original\r\n"
                );
                Ok(())
            })
            .unwrap();
    }

    #[tokio::test]
    async fn incompatible_service_refuses_before_discovery_or_any_local_attempt() {
        let mut incompatible = handshake();
        incompatible["encryption"] = json!("e2ee");
        let (endpoint, server) = origin(vec![incompatible]);
        let root = tempfile::tempdir().unwrap();
        let mut ws = Workspace::open(root.path()).unwrap();
        let binding = ws.sync_bind_empty(&endpoint, "remote", "account").unwrap();
        ws.write("original.md", "", b"unchanged", "local").unwrap();
        let workspace = Arc::new(Mutex::new(ws));
        let error = run(
            SyncClient::new(&endpoint, Zeroizing::new("owned-test-only".into()), true).unwrap(),
            &workspace,
            &binding,
            Arc::new(|_| panic!("Incompatible handshake must not start discovery")),
        )
        .await
        .unwrap_err();
        assert_eq!(error.code, "SYNC_ENCRYPTION_INCOMPATIBLE");
        assert_eq!(server.join().unwrap().len(), 1);
        workspace
            .access(|ws| {
                assert!(ws.sync_attempts(&binding.id)?.is_empty());
                assert_eq!(ws.sync_binding()?.unwrap().cursor, 0);
                assert_eq!(ws.read("original.md")?.content, "unchanged");
                Ok(())
            })
            .unwrap();
    }
}
