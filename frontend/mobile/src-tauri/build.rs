fn main() {
    tauri_build::try_build(tauri_build::Attributes::new().app_manifest(
        tauri_build::AppManifest::new().commands(&[
            "mobile_capabilities",
            "mobile_vault",
            "mobile_request",
            "mobile_sync_auth",
            "mobile_sync_association",
            "mobile_sync_cycle",
            "mobile_save_export",
            "mobile_export_delivery",
            "mobile_open_external",
            "mobile_background",
            "mobile_appearance",
        ]),
    ))
    .expect("Invalid OpenNexus mobile configuration");
}
