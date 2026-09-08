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
