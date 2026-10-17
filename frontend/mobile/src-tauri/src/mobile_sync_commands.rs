//! One main-window command gate for the Android-native Auth coordinator.
//! No access/refresh token, serialized Session or general credential read IPC.
use crate::{main_window, mobile_sync_secrets::NativeSecretStore, mobile_sync_tls, MobileState};
use notesagent_host::{
    mobile_sync_activity::{Admission, Command, Reply, Snapshot, Store},
    mobile_sync_auth::{Auth, HttpsTransport},
    mobile_sync_commands::{execute, public_error, Request, Response},
    mobile_sync_foreground::Lease,
};
use std::path::Path;
use std::sync::Arc;
use std::time::{SystemTime, UNIX_EPOCH};
use tauri::async_runtime::Mutex;
use tauri::Emitter;

#[derive(Default)]
pub(crate) struct Runtime {
    gate: Mutex<Option<Auth<&'static NativeSecretStore>>>,
    activity: Store,
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
        state.sync.activity.clear_identity().map_err(public_error)?;
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

/// All cycle commands share the original Auth broker. Live RAM observation and
/// Stop deliberately bypass its long-held async gate so Stop can interrupt IO.
pub(crate) async fn cycle(
    window: tauri::WebviewWindow,
    state: tauri::State<'_, MobileState>,
    request: Command,
) -> Result<Reply, String> {
    use notesagent_host::{mobile_sync_cycle, sync_client::SyncError};
    main_window(&window)?;
    request
        .validate()
        .map_err(mobile_sync_cycle::public_error)?;
    let (request, mut ticket) = match request {
        Command::Activity {
            target,
            invocation_id,
        } => {
            return Ok(Reply::Activity {
                snapshot: state
                    .sync
                    .activity
                    .snapshot(&target, &invocation_id)
                    .map_err(mobile_sync_cycle::public_error)?,
            });
        }
        Command::Stop {
            target,
            invocation_id,
        } => {
            return Ok(Reply::Stopped {
                state: state
                    .sync
                    .activity
                    .stop(&target, &invocation_id, cycle_time()?)
                    .map_err(mobile_sync_cycle::public_error)?,
            });
        }
        Command::Run {
            request,
            invocation_id,
        } => {
            // Capture before admission and before the Auth-gate wait. A queued
            // request can never capture a fresh post-login/onStart identity.
            let lease = mobile_sync_tls::foreground()
                .lease()
                .map_err(mobile_sync_cycle::public_error)?;
            let ticket = if let Some(invocation_id) = invocation_id {
                match state
                    .sync
                    .activity
                    .admit(
                        &lease,
                        request.target().clone(),
                        invocation_id,
                        cycle_time()?,
                    )
                    .map_err(mobile_sync_cycle::public_error)?
                {
                    Admission::Known(snapshot) => return Ok(Reply::Activity { snapshot }),
                    Admission::Started(ticket) => Some(ticket),
                }
            } else {
                None
            };
            ((lease, request), ticket)
        }
    };
    let (lease, request) = request;
    let emit_window = window.clone();
    let deliver: Arc<dyn Fn(Snapshot) + Send + Sync> = Arc::new(move |snapshot| {
        // Emitter::emit broadcasts; target the owning main WebView explicitly.
        let _ = emit_window.emit_to(
            tauri::EventTarget::webview_window("main"),
            "mobile-sync-activity",
            snapshot,
        );
    });
    let report = ticket
        .as_ref()
        .map(|ticket| ticket.collector(deliver.clone()))
        .unwrap_or_else(|| Arc::new(|_| {}));
    let result = async {
        let mut guard = lease
            .guard(async { Ok(state.sync.gate.lock().await) })
            .await?;
        let auth =
            auth(&mut guard, &lease, &state.private_root).map_err(|code| SyncError::new(&code))?;
        lease
            .guard(mobile_sync_cycle::execute(
                auth,
                &lease,
                state.engine.clone(),
                request,
                now().map_err(|code| SyncError::new(&code))?,
                report,
            ))
            .await
    }
    .await;
    if let Some(ticket) = ticket.as_mut() {
        let snapshot = ticket
            .finish(
                cycle_time()?,
                result.as_ref().err().map(|error| error.code.as_str()),
            )
            .map_err(mobile_sync_cycle::public_error)?;
        // Final metadata uses the same original lifecycle guard as progress.
        // Stop/background/identity changes return Inactive and emit nothing.
        if matches!(snapshot, Snapshot::Known(_)) {
            let _ = lease.with_current(|| {
                deliver(snapshot);
                Ok(())
            });
        }
    }
    result
        .map(|response| Reply::Result {
            response: Box::new(response),
        })
        .map_err(mobile_sync_cycle::public_error)
}

fn cycle_time() -> Result<i64, String> {
    i64::try_from(now()?).map_err(|_| "SYNC_TIME_INVALID".into())
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
