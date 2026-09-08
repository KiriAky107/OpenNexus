fn main() {
    let manifest = std::path::Path::new("../../.build/sidecar/manifest.json");
    println!("cargo:rerun-if-changed={}", manifest.display());
    let content = if std::env::var("PROFILE").as_deref() == Ok("release") {
        std::fs::read(manifest)
            .expect("Build Core with scripts/build-core.py before a release Host")
    } else {
        b"{}".to_vec()
    };
    std::fs::write(
        std::path::Path::new(&std::env::var("OUT_DIR").unwrap()).join("core-manifest.json"),
        content,
    )
    .unwrap();
    #[cfg(feature = "desktop")]
    tauri_build::try_build(tauri_build::Attributes::new().app_manifest(
        tauri_build::AppManifest::new().commands(&[
            "host_capabilities",
            "sync_login",
            "sync_vaults",
            "sync_create_vault",
            "sync_bind",
            "sync_unbind",
            "sync_pause",
            "sync_status",
            "sync_resolve",
            "sync_logout",
            "sync_run",
            "credentials_status",
            "credentials_unlock",
            "credentials_lock",
            "credentials_change_password",
            "credentials_import",
            "credentials_backup",
            "credentials_restore",
            "core_request",
            "core_request_prepare",
            "core_request_cancel",
            "core_stream",
            "core_stream_cancel",
            "editor_capabilities",
            "workspace_choose",
            "workspace_open",
            "workspace_tree",
            "workspace_read",
            "workspace_write",
            "workspace_operation",
            "workspace_rename",
            "workspace_delete",
            "workspace_mkdir",
            "workspace_recent",
            "workspace_revoke",
        ]),
    ))
    .expect("Tauri 配置无效");
}
