fn main() {
    #[cfg(feature = "desktop")]
    tauri_build::try_build(tauri_build::Attributes::new().app_manifest(
        tauri_build::AppManifest::new().commands(&[
            "host_capabilities",
            "core_request",
            "editor_capabilities",
            "workspace_choose",
            "workspace_open",
            "workspace_tree",
            "workspace_read",
            "workspace_write",
            "workspace_rename",
            "workspace_delete",
            "workspace_mkdir",
            "workspace_recent",
            "workspace_revoke",
        ]),
    ))
    .expect("Tauri 配置无效");
}
