use notesagent_host::core::verify_bundle;

#[test]
fn bundle_rejects_modified_missing_and_extra_files() {
    let dir = tempfile::tempdir().unwrap();
    let file = dir.path().join("opennexus-core.exe");
    std::fs::write(&file, b"abc").unwrap();
    let manifest = r#"{"protocol":1,"product":"OpenNexus","files":{"opennexus-core.exe":"ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"}}"#;
    verify_bundle(dir.path(), manifest).unwrap();
    std::fs::write(&file, b"abd").unwrap();
    assert_eq!(
        verify_bundle(dir.path(), manifest).unwrap_err(),
        "CORE_INTEGRITY_FAILED"
    );
    std::fs::write(&file, b"abc").unwrap();
    std::fs::write(dir.path().join("injected.dll"), b"malicious").unwrap();
    assert!(verify_bundle(dir.path(), manifest).is_err());
    std::fs::remove_file(dir.path().join("injected.dll")).unwrap();
    std::fs::remove_file(file).unwrap();
    assert!(verify_bundle(dir.path(), manifest).is_err());
}

#[test]
#[ignore = "requires scripts/build-core.py; run explicitly after building the isolated Core"]
fn packaged_core_twenty_cold_starts_use_isolated_data_and_rotate_sessions() {
    use notesagent_host::core::CoreSupervisor;
    use std::{
        collections::HashSet,
        io::{Read, Write},
        path::Path,
        time::Duration,
    };
    let output = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../.build/sidecar");
    let bundle = output.join("dist/opennexus-core").canonicalize().unwrap();
    let manifest = std::fs::read_to_string(output.join("manifest.json")).unwrap();
    let executable = bundle.join(if cfg!(windows) {
        "opennexus-core.exe"
    } else {
        "opennexus-core"
    });
    let temp = tempfile::tempdir().unwrap();
    let mut generations = HashSet::new();
    for attempt in 0..20 {
        let mut core = CoreSupervisor::new(
            executable.clone(),
            vec![],
            bundle.clone(),
            temp.path().join(format!("run-{attempt}")),
        )
        .with_bundle_manifest(manifest.clone());
        let request = core.request_session("/health").unwrap();
        assert!(generations.insert(request.generation.clone()));
        let endpoint = request
            .url
            .trim_start_matches("http://")
            .trim_end_matches("/health");
        let mut stream = std::net::TcpStream::connect(endpoint).unwrap();
        stream
            .set_read_timeout(Some(Duration::from_secs(20)))
            .unwrap();
        write!(stream, "GET /health HTTP/1.1\r\nHost: {endpoint}\r\nAuthorization: {}\r\nX-Core-Generation: {}\r\nConnection: close\r\n\r\n", request.authorization.as_str(), request.generation).unwrap();
        let mut response = String::new();
        stream.read_to_string(&mut response).unwrap();
        assert!(
            response.starts_with("HTTP/1.1 200"),
            "packaged health failed on attempt {attempt}"
        );
        assert!(!response.contains(request.authorization.as_str()));
        drop(stream);
        drop(core);
        assert!(std::net::TcpStream::connect(endpoint).is_err());
    }
}
