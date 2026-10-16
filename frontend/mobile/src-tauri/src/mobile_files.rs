use notesagent_host::mobile_export;
use serde::Serialize;
use tauri::Manager;

#[cfg(target_os = "android")]
fn native_error(error: impl std::fmt::Display, fallback: &str) -> String {
    let message = error.to_string();
    if [
        "MOBILE_EXPORT_PICKER_FAILED",
        "MOBILE_EXPORT_BUSY",
        "MOBILE_EXPORT_RESULT_UNKNOWN",
        "MOBILE_LINK_UNSUPPORTED",
        "MOBILE_LINK_OPEN_FAILED",
    ]
    .contains(&message.as_str())
    {
        message
    } else {
        fallback.into()
    }
}

#[cfg(target_os = "android")]
struct FilesPlugin(tauri::plugin::PluginHandle<tauri::Wry>);

pub fn init() -> tauri::plugin::TauriPlugin<tauri::Wry> {
    tauri::plugin::Builder::new("mobile-files")
        .setup(|app, api| {
            #[cfg(target_os = "android")]
            app.manage(FilesPlugin(api.register_android_plugin(
                "cc.kronecker.opennexus.files",
                "MobileFilesPlugin",
            )?));
            #[cfg(not(target_os = "android"))]
            let _ = (app, api);
            Ok(())
        })
        .build()
}

#[derive(Serialize)]
struct ExportCall<'a> {
    ticket: &'a str,
    delivery_id: Option<&'a str>,
}

async fn checked_export(
    state: &super::MobileState,
    ticket: String,
) -> Result<mobile_export::ExportReceipt, String> {
    let root = state.private_root.clone();
    tauri::async_runtime::spawn_blocking(move || {
        mobile_export::verify_export(&root, &ticket).map_err(|e| e.code)
    })
    .await
    .map_err(|_| "MOBILE_HOST_INTERRUPTED".to_owned())?
}

#[tauri::command]
pub async fn mobile_save_export(
    window: tauri::WebviewWindow,
    app: tauri::AppHandle,
    state: tauri::State<'_, super::MobileState>,
    ticket: String,
) -> Result<super::MobileResponse, String> {
    super::main_window(&window)?;
    let receipt = checked_export(&state, ticket).await?;
    #[cfg(target_os = "android")]
    {
        let plugin = app.state::<FilesPlugin>().0.clone();
        plugin
            .run_mobile_plugin_async(
                "saveExport",
                ExportCall {
                    ticket: &receipt.ticket,
                    delivery_id: None,
                },
            )
            .await
            .map_err(|error| native_error(error, "MOBILE_EXPORT_RESULT_UNKNOWN"))
    }
    #[cfg(not(target_os = "android"))]
    {
        let _ = (app, receipt);
        Err("MOBILE_PLATFORM_UNSUPPORTED".into())
    }
}

#[tauri::command]
pub async fn mobile_export_delivery(
    window: tauri::WebviewWindow,
    app: tauri::AppHandle,
    state: tauri::State<'_, super::MobileState>,
    ticket: String,
    delivery_id: Option<String>,
) -> Result<super::MobileResponse, String> {
    super::main_window(&window)?;
    let receipt = checked_export(&state, ticket).await?;
    #[cfg(target_os = "android")]
    {
        let plugin = app.state::<FilesPlugin>().0.clone();
        plugin
            .run_mobile_plugin_async(
                "readDelivery",
                ExportCall {
                    ticket: &receipt.ticket,
                    delivery_id: delivery_id.as_deref(),
                },
            )
            .await
            .map_err(|error| native_error(error, "MOBILE_EXPORT_RESULT_UNKNOWN"))
    }
    #[cfg(not(target_os = "android"))]
    {
        let _ = (app, receipt, delivery_id);
        Err("MOBILE_PLATFORM_UNSUPPORTED".into())
    }
}

#[derive(Serialize)]
struct LinkCall<'a> {
    href: &'a str,
}

#[tauri::command]
pub async fn mobile_open_external(
    window: tauri::WebviewWindow,
    app: tauri::AppHandle,
    href: String,
) -> Result<super::MobileResponse, String> {
    super::main_window(&window)?;
    let parsed = tauri::Url::parse(&href).map_err(|_| "MOBILE_LINK_UNSUPPORTED".to_owned())?;
    if href.len() > 4096
        || href.chars().any(char::is_whitespace)
        || !matches!(parsed.scheme(), "https" | "http")
        || parsed.host_str().is_none()
        || !parsed.username().is_empty()
        || parsed.password().is_some()
    {
        return Err("MOBILE_LINK_UNSUPPORTED".into());
    }
    #[cfg(target_os = "android")]
    {
        let plugin = app.state::<FilesPlugin>().0.clone();
        plugin
            .run_mobile_plugin_async("openExternal", LinkCall { href: &href })
            .await
            .map_err(|error| native_error(error, "MOBILE_LINK_OPEN_FAILED"))
    }
    #[cfg(not(target_os = "android"))]
    {
        let _ = app;
        Err("MOBILE_PLATFORM_UNSUPPORTED".into())
    }
}
