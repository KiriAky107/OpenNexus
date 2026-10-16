fn main() {
    tauri_build::try_build(tauri_build::Attributes::new().app_manifest(
        tauri_build::AppManifest::new().commands(&[
            "mobile_capabilities",
            "mobile_vault",
            "mobile_request",
        ]),
    ))
    .expect("Invalid OpenNexus mobile configuration");
}
