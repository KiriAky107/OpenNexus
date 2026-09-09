//! 执行真实的工作树Core，没有个人数据或外部提供者。
use notesagent_host::core::CoreSupervisor;
use notesagent_host::credentials::{CredentialBroker, CredentialId, Scope};
use std::path::Path;
use std::sync::{Arc, Mutex};
use zeroize::Zeroizing;

#[test]
fn real_python_core_authenticates_and_rotates_generation() {
    let backend = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../backend")
        .canonicalize()
        .unwrap();
    let python = backend.join(if cfg!(windows) {
        ".venv/Scripts/python.exe"
    } else {
        ".venv/bin/python"
    });
    assert!(
        python.is_file(),
        "Create the isolated backend environment with uv sync --frozen"
    );
    let temp = tempfile::tempdir().unwrap();
    let mut first = CoreSupervisor::new(
        python.clone(),
        vec!["-m".into(), "app.sidecar".into()],
        backend.clone(),
        temp.path().join("one"),
    );
    let request = first.request_session("/health").unwrap();
    assert!(first.available());
    assert!(request.url.starts_with("http://127.0.0.1:"));
    assert!(!request.url.contains(request.authorization.as_str()));
    let mut second = CoreSupervisor::new(
        python,
        vec!["-m".into(), "app.sidecar".into()],
        backend,
        temp.path().join("two"),
    );
    let other = second.request_session("/health").unwrap();
    assert_ne!(request.generation, other.generation);
    assert_ne!(request.authorization.as_str(), other.authorization.as_str());
    drop(first);
    let endpoint = request
        .url
        .trim_start_matches("http://")
        .trim_end_matches("/health");
    assert!(std::net::TcpStream::connect(endpoint).is_err());
}

#[test]
fn real_core_credential_api_uses_host_stronghold_without_plaintext_response() {
    use std::io::{Read, Write};
    let backend = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../backend")
        .canonicalize()
        .unwrap();
    let python = backend.join(if cfg!(windows) {
        ".venv/Scripts/python.exe"
    } else {
        ".venv/bin/python"
    });
    let temp = tempfile::tempdir().unwrap();
    let broker = Arc::new(Mutex::new(CredentialBroker::new(
        temp.path().join("stronghold.v1"),
    )));
    broker
        .lock()
        .unwrap()
        .unlock(Zeroizing::new(b"controlled-fixture-password".to_vec()))
        .unwrap();
    let handler = broker.clone();
    let mut core = CoreSupervisor::new(
        python,
        vec!["-m".into(), "app.sidecar".into()],
        backend,
        temp.path().join("core"),
    )
    .with_broker(Arc::new(move |request| {
        handler.lock().unwrap().dispatch(request)
    }));
    let session = core
        .request_session("/api/credentials/fixture-provider")
        .unwrap();
    let endpoint = session
        .url
        .trim_start_matches("http://")
        .split('/')
        .next()
        .unwrap();
    let body = r#"{"api_key":"fixture-credential-via-host-pipe"}"#;
    let mut socket = std::net::TcpStream::connect(endpoint).unwrap();
    socket
        .set_read_timeout(Some(std::time::Duration::from_secs(30)))
        .unwrap();
    write!(socket, "PUT /api/credentials/fixture-provider HTTP/1.1\r\nHost: {endpoint}\r\nAuthorization: {}\r\nX-Core-Generation: {}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}", session.authorization.as_str(), session.generation, body.len()).unwrap();
    let mut response = String::new();
    socket.read_to_string(&mut response).unwrap();
    assert!(
        response.starts_with("HTTP/1.1 200"),
        "credential API did not succeed"
    );
    assert!(!response.contains("fixture-credential-via-host-pipe"));
    let status_session = core
        .request_session("/api/credentials/fixture-provider")
        .unwrap();
    let mut socket = std::net::TcpStream::connect(endpoint).unwrap();
    socket
        .set_read_timeout(Some(std::time::Duration::from_secs(30)))
        .unwrap();
    write!(socket, "GET /api/credentials/fixture-provider HTTP/1.1\r\nHost: {endpoint}\r\nAuthorization: {}\r\nX-Core-Generation: {}\r\nConnection: close\r\n\r\n", status_session.authorization.as_str(), status_session.generation).unwrap();
    let mut status_response = String::new();
    socket.read_to_string(&mut status_response).unwrap();
    assert!(status_response.starts_with("HTTP/1.1 200"));
    assert!(status_response.contains("\"credential_id\":\"fixture-provider\""));
    assert!(status_response.contains("\"configured\":true"));
    assert!(!status_response.contains("fixture-credential-via-host-pipe"));
    let id = CredentialId {
        scope: Scope::Provider,
        id: "fixture-provider".into(),
    };
    assert_eq!(
        broker
            .lock()
            .unwrap()
            .resolve(&Scope::Provider, &id)
            .unwrap()
            .as_deref()
            .map(|s| s.as_slice()),
        Some(b"fixture-credential-via-host-pipe".as_slice())
    );
    assert!(!temp.path().join("core/credentials/master.key").exists());
    assert!(!temp
        .path()
        .join("core/credentials/credentials.json")
        .exists());
}
