//! One main-window command gate for the Android-native Auth coordinator.
//! No access/refresh token, serialized Session or general credential read IPC.
use crate::{main_window, mobile_sync_secrets::NativeSecretStore, mobile_sync_tls, MobileState};
use notesagent_host::{
    mobile_sync_auth::{Auth, HttpsTransport},
    mobile_sync_commands::{execute, public_error, Request, Response},
};
use std::time::{SystemTime, UNIX_EPOCH};
use tauri::async_runtime::Mutex;

#[derive(Default)]
pub(crate) struct Runtime {
    gate: Mutex<Option<Auth<&'static NativeSecretStore>>>,
}

pub(crate) async fn request(
    window: tauri::WebviewWindow,
    state: tauri::State<'_, MobileState>,
    request: Request,
) -> Result<Response, String> {
    main_window(&window)?;
    let foreground = mobile_sync_tls::foreground();
    if request.invalidates_identity() {
        // Cancel an active task and every previously queued lease before
        // waiting. Capturing a new lease only after the lock could revive an
        // old request following a newer account switch or Activity.onStop.
        foreground.invalidate_identity();
    }
    let lease = foreground.lease().map_err(public_error)?;
    let mut guard = lease
        .guard(async { Ok(state.sync.gate.lock().await) })
        .await
        .map_err(public_error)?;
    if guard.is_none() {
        let store = NativeSecretStore::global().map_err(|error| error.0.to_owned())?;
        *guard = Some(
            lease
                .with_current(|| Auth::open(&state.private_root, store))
                .map_err(public_error)?,
        );
    }
    let auth = guard.as_mut().ok_or("SYNC_CREDENTIALS_UNAVAILABLE")?;
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|_| "SYNC_TIME_INVALID")?
        .as_secs();
    execute(auth, &lease, request, &HttpsTransport, now)
        .await
        .map_err(public_error)
}
