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
    use notesagent_host::workspace::Workspace;
    use std::{
        collections::HashSet,
        io::{Read, Write},
        path::Path,
        time::{Duration, Instant},
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
    let vault = temp.path().join("vault");
    std::fs::create_dir(&vault).unwrap();
    let mut workspace = Workspace::open(&vault).unwrap();
    let mut generations = HashSet::new();
    let mut ready_times = Vec::new();
    for attempt in 0..20 {
        let mut core = CoreSupervisor::new(
            executable.clone(),
            vec![],
            bundle.clone(),
            temp.path().join(format!("run-{attempt}")),
        )
        .with_bundle_manifest(manifest.clone());
        let started = Instant::now();
        let request = core.request_session("/health").unwrap();
        ready_times.push(started.elapsed());
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
        let path = format!("cold-start-{attempt}.md");
        let saved = workspace
            .write(&path, "", format!("本地编辑 {attempt}").as_bytes(), "local")
            .unwrap();
        assert_eq!(workspace.read(&path).unwrap().entry.hash, saved.hash);
        drop(stream);
        drop(core);
        assert!(std::net::TcpStream::connect(endpoint).is_err());
    }
    ready_times.sort();
    let p95 = ready_times[18];
    eprintln!("20 次打包 Core 冷启动 ready P95: {p95:?}");
    assert!(p95 <= Duration::from_secs(10), "ready P95 超过 10 秒");
}

#[test]
#[ignore = "需要先通过 scripts/build-core.py 构建隔离 Core"]
fn one_byte_tampered_packaged_core_is_refused_twenty_times() {
    use notesagent_host::core::CoreSupervisor;
    use sha2::{Digest, Sha256};
    let output = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../../.build/sidecar");
    let source = output.join("dist/opennexus-core/opennexus-core.exe");
    let temp = tempfile::tempdir().unwrap();
    let root = temp.path().join("core");
    std::fs::create_dir(&root).unwrap();
    let executable = root.join("opennexus-core.exe");
    std::fs::copy(&source, &executable).unwrap();
    let original = std::fs::read(&executable).unwrap();
    let manifest = serde_json::json!({
        "protocol": 1,
        "product": "OpenNexus",
        "files": {"opennexus-core.exe": format!("{:x}", Sha256::digest(&original))}
    })
    .to_string();
    let mut tampered = original;
    tampered[0] ^= 1;
    std::fs::write(&executable, tampered).unwrap();
    for attempt in 0..20 {
        let mut core = CoreSupervisor::new(
            executable.clone(),
            vec![],
            root.clone(),
            temp.path().join(format!("tampered-{attempt}")),
        )
        .with_bundle_manifest(manifest.clone());
        assert_eq!(core.start().unwrap_err(), "CORE_INTEGRITY_FAILED");
        assert!(core.process_id().is_none());
    }
}
