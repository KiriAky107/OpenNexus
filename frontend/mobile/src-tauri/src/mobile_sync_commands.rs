//! One main-window command gate for the Android-native Auth coordinator.
//! No access/refresh token, serialized Session or general credential read IPC.
use crate::{main_window, mobile_sync_secrets::NativeSecretStore, mobile_sync_tls, MobileState};
use notesagent_host::{
    mobile_sync_auth::{Auth, HttpsTransport},
    mobile_sync_commands::{execute, public_error, Request, Response},
    mobile_sync_foreground::Lease,
};
use std::path::Path;
use std::time::{SystemTime, UNIX_EPOCH};
use tauri::async_runtime::Mutex;

#[derive(Default)]
pub(crate) struct Runtime {
    gate: Mutex<Option<Auth<&'static NativeSecretStore>>>,
}

fn auth<'a>(
    slot: &'a mut Option<Auth<&'static NativeSecretStore>>,
    lease: &Lease,
    root: &Path,
) -> Result<&'a mut Auth<&'static NativeSecretStore>, String> {
    if slot.is_none() {
        let store = NativeSecretStore::global().map_err(|error| error.0.to_owned())?;
        *slot = Some(
            lease
                .with_current(|| Auth::open(root, store))
                .map_err(public_error)?,
        );
    }
    slot.as_mut()
        .ok_or_else(|| "SYNC_CREDENTIALS_UNAVAILABLE".to_owned())
}
fn now() -> Result<u64, String> {
    Ok(SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|_| "SYNC_TIME_INVALID")?
        .as_secs())
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
    let auth = auth(&mut guard, &lease, &state.private_root)?;
    execute(auth, &lease, request, &HttpsTransport, now()?)
        .await
        .map_err(public_error)
}

pub(crate) async fn association(
    window: tauri::WebviewWindow,
    state: tauri::State<'_, MobileState>,
    request: notesagent_host::mobile_sync_association::Request,
) -> Result<notesagent_host::mobile_sync_association::Response, String> {
    main_window(&window)?;
    let lease = mobile_sync_tls::foreground()
        .lease()
        .map_err(public_error)?;
    // Share the exact Auth gate/broker with sign-in, refresh, logout and account
    // selection. Their pre-gate invalidation cancels this original lease too.
    let mut guard = lease
        .guard(async { Ok(state.sync.gate.lock().await) })
        .await
        .map_err(public_error)?;
    let auth = auth(&mut guard, &lease, &state.private_root)?;
    lease
        .guard(notesagent_host::mobile_sync_association::execute(
            auth,
            &lease,
            state.engine.clone(),
            request,
            now()?,
        ))
        .await
        .map_err(notesagent_host::mobile_sync_association::public_error)
}
