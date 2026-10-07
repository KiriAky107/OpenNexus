use super::*;
use serde_json::json;
use std::{
    io::{BufRead, BufReader},
    path::PathBuf,
    process::{Child, Command, Stdio},
    sync::mpsc,
};

#[test]
fn exact_version_pages_reject_inconsistent_or_partial_metadata() {
    for (value, accepted) in [
        (json!({"items": []}), true),
        (
            json!({"items": [], "schema_version":1,"total":0,"offset":0,"limit":100}),
            true,
        ),
        (
            json!({"items": [], "schema_version":2,"total":0,"offset":0,"limit":100}),
            false,
        ),
        (
            json!({"items": [], "schema_version":1,"total":135,"offset":0,"limit":100}),
            false,
        ),
        (
            json!({"items": [], "schema_version":1,"total":0,"offset":100,"limit":100}),
            false,
        ),
        (json!({"items": [], "schema_version":1}), false),
        (
            json!({"items": [], "schema_version":1,"total":0,"offset":0,"limit":30}),
            false,
        ),
    ] {
        let result: Releases = serde_json::from_value(value).unwrap();
        assert_eq!(result.validate().is_ok(), accepted);
    }
    assert!(serde_json::from_value::<Releases>(json!({"items":[],"unknown":true})).is_err());
}

#[test]
fn shared_catalog_versions_are_canonical_and_compatibility_is_not_inverted() {
    let contract: Value = serde_json::from_str(include_str!(
        "../../src/services/fixtures/community-v1-catalog.json"
    ))
    .unwrap();
    let mut fixture: Value = serde_json::from_str(include_str!(
        "../../src/services/fixtures/community-python-vector.json"
    ))
    .unwrap();
    for field in ["release_id", "withdrawn", "download_path"] {
        fixture["release"].as_object_mut().unwrap().remove(field);
    }
    let mut release: Release = serde_json::from_value(fixture["release"].clone()).unwrap();
    for version in contract["versions"].as_array().unwrap() {
        release.version = version.as_str().unwrap().into();
        release.validate().unwrap();
    }
    for version in contract["invalid"].as_array().unwrap() {
        release.version = version.as_str().unwrap().into();
        assert!(release.validate().is_err(), "{version}");
    }
    release.version = "1.0.0+build.1".into();
    release.min_app_version = "0.6.0".into();
    release.max_app_version = Some("0.5.9".into());
    assert!(release.validate().is_err());
    release.min_app_version = "0.6.0+build.9".into();
    release.max_app_version = Some("0.6.0+build.1".into());
    release.validate().unwrap();
}

struct OwnedServer(Child);
impl Drop for OwnedServer {
    fn drop(&mut self) {
        self.0.stdin.take();
        let until = Instant::now() + Duration::from_secs(2);
        while Instant::now() < until {
            if self.0.try_wait().ok().flatten().is_some() {
                return;
            }
            std::thread::sleep(Duration::from_millis(20));
        }
        let _ = self.0.kill();
        let _ = self.0.wait();
    }
}

#[tokio::test]
#[ignore = "requires scripts/check_community_tls.py process-scoped HTTPS fixture"]
async fn actual_https_roots_require_trust_hostname_and_validity() {
    let endpoints: Value = serde_json::from_str(
        &std::env::var("OPENNEXUS_COMMUNITY_TLS_ENDPOINTS").expect("owned TLS fixture is required"),
    )
    .unwrap();
    let trusted = Client::new(endpoints["trusted"].as_str().unwrap()).unwrap();
    let source = trusted.json("catalog/v1/sources").await.unwrap();
    assert_eq!(source["source_id"], "owned-tls-fixture");
    for name in ["untrusted", "mismatch", "expired"] {
        let client = Client::new(endpoints[name].as_str().unwrap()).unwrap();
        assert_eq!(
            client.json("catalog/v1/sources").await.err().unwrap().code,
            "EXTENSION_TRUST_UNAVAILABLE",
            "{name} certificate must be rejected"
        );
    }
    assert_eq!(
        trusted.json("redirect").await.err().unwrap().code,
        "EXTENSION_TRUST_UNAVAILABLE"
    );
    assert_eq!(
        Client::new("http://127.0.0.1/").err().unwrap().code,
        "EXTENSION_SOURCE_INVALID"
    );
    let mut request = crate::community_catalog::Request {
        source: endpoints["trusted"].as_str().unwrap().into(),
        path: "/catalog/v1/packages?q=fixture&limit=30".into(),
        max_bytes: 1024,
        if_none_match: None,
    };
    let page = crate::community_catalog::fetch(&request).await.unwrap();
    assert_eq!(page.status, 200);
    assert!(page.body_base64.is_some());
    assert_eq!(page.etag.as_deref(), Some("\"owned-page\""));
    request.if_none_match = page.etag;
    let unchanged = crate::community_catalog::fetch(&request).await.unwrap();
    assert_eq!(unchanged.status, 304);
    assert!(unchanged.body_base64.is_none());
    request.path = "/catalog/v1/sources".into();
    request.if_none_match = None;
    request.max_bytes = 8;
    assert_eq!(
        crate::community_catalog::fetch(&request)
            .await
            .err()
            .unwrap()
            .code,
        "COMMUNITY_RESPONSE_LIMIT"
    );
    request.path = "/catalog/v1/releases/redirect".into();
    request.max_bytes = 1024;
    let redirect = crate::community_catalog::fetch(&request).await.unwrap();
    assert_eq!(redirect.status, 302);
    assert!(redirect.body_base64.is_none());
}

#[tokio::test]
#[ignore = "requires OPENNEXUS_COMMUNITY_SERVER_DIR with frozen fixture dependencies"]
async fn actual_community_old_version_signature_archive_and_withdrawal() {
    let service = PathBuf::from(
        std::env::var_os("OPENNEXUS_COMMUNITY_SERVER_DIR")
            .expect("fixed Community fixture is required"),
    )
    .canonicalize()
    .unwrap();
    let python = service.join(if cfg!(windows) {
        ".venv/Scripts/python.exe"
    } else {
        ".venv/bin/python"
    });
    let root = tempfile::tempdir().unwrap();
    std::fs::write(root.path().join(".opennexus-test"), b"owned-fixture").unwrap();
    let mut server = OwnedServer(
        Command::new(python)
            .args(["-m", "tests.host_fixture"])
            .arg(root.path())
            .arg("--family")
            .current_dir(&service)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::inherit())
            .spawn()
            .unwrap(),
    );
    let stdout = server.0.stdout.take().unwrap();
    let (send, receive) = mpsc::channel();
    let reader = std::thread::spawn(move || {
        let mut line = String::new();
        let result = BufReader::new(stdout).read_line(&mut line).map(|_| line);
        let _ = send.send(result);
    });
    let line = receive
        .recv_timeout(Duration::from_secs(15))
        .unwrap()
        .unwrap();
    reader.join().unwrap();
    let ready: Value = serde_json::from_str(&line).unwrap();
    let public: [u8; 32] = STANDARD
        .decode(ready["key"]["public_key"].as_str().unwrap())
        .unwrap()
        .try_into()
        .unwrap();
    let mut client = Client::new("https://catalog.example/").unwrap();
    // Only this crate-owned test can override the HTTPS production client.
    client.source = reqwest::Url::parse(&format!("http://127.0.0.1:{}/", ready["port"])).unwrap();
    let first = client
        .json("catalog/v1/packages/examples/version-family/releases")
        .await
        .unwrap();
    assert_eq!(first["total"], 135);
    assert_eq!(first["items"].as_array().unwrap().len(), 100);
    assert!(first["items"]
        .as_array()
        .unwrap()
        .iter()
        .all(|item| item["version"] != "1.0.0+build.1"));
    let mut selected = client
        .json("catalog/v1/releases/fixture-0000")
        .await
        .unwrap();
    for field in ["release_id", "withdrawn", "download_path"] {
        selected.as_object_mut().unwrap().remove(field);
    }
    let release: Release = serde_json::from_value(selected).unwrap();
    let pin = || Pin {
        source_id: "catalog-host-fixture",
        key_id: "fixture-key",
        namespace: "examples",
        public_key: &public,
    };
    let checked = client.check(pin(), &release).await.unwrap();
    let archive = client.download(&checked, &release, &public).await.unwrap();
    assert_eq!(archive.len() as u64, release.size);
    assert_eq!(hash(&archive), release.sha256);
    let db = rusqlite::Connection::open(root.path().join("catalog.sqlite3")).unwrap();
    db.execute(
        "UPDATE submissions SET state='withdrawn' WHERE id='fixture-0000'",
        [],
    )
    .unwrap();
    assert_eq!(
        client.check(pin(), &release).await.err().unwrap().code,
        "EXTENSION_RELEASE_WITHDRAWN"
    );
    db.execute(
        "UPDATE submissions SET state='published' WHERE id='fixture-0000'",
        [],
    )
    .unwrap();
    db.execute("UPDATE keys SET revoked=1 WHERE id='fixture-key'", [])
        .unwrap();
    assert_eq!(
        client.check(pin(), &release).await.err().unwrap().code,
        "EXTENSION_KEY_REVOKED"
    );
    db.execute("UPDATE keys SET revoked=0 WHERE id='fixture-key'", [])
        .unwrap();
    let mut changed = release.clone();
    changed.description.push_str(" changed permissions review");
    assert_eq!(
        client.check(pin(), &changed).await.err().unwrap().code,
        "EXTENSION_TRUST_CHANGED"
    );
    println!("ACTUAL_COMMUNITY_FIXTURE_VERIFIED total=135 selected=1.0.0+build.1 bytes={} withdrawal=refused revoked=refused changed=refused",archive.len());
    drop(db);
    server.0.stdin.take();
    let until = Instant::now() + Duration::from_secs(5);
    loop {
        if let Some(status) = server.0.try_wait().unwrap() {
            assert!(status.success());
            break;
        }
        assert!(
            Instant::now() < until,
            "owned fixture did not stop after stdin closed"
        );
        std::thread::sleep(Duration::from_millis(20));
    }
}
