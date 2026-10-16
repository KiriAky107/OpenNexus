fn main() {
    tauri_build::try_build(tauri_build::Attributes::new().app_manifest(
        tauri_build::AppManifest::new().commands(&[
            "mobile_capabilities",
            "mobile_vault",
            "mobile_request",
            "mobile_save_export",
            "mobile_export_delivery",
            "mobile_open_external",
        ]),
    ))
    .expect("Invalid OpenNexus mobile configuration");
}
