//! Native startup bridge; it is deliberately not a WebView command.
use jni::{errors::ThrowRuntimeExAndDefault, objects::JObject, EnvUnowned};
use notesagent_host::mobile_sync_foreground::Foreground;
use std::sync::OnceLock;

static FOREGROUND: OnceLock<Foreground> = OnceLock::new();
pub(crate) fn foreground() -> &'static Foreground {
    FOREGROUND.get_or_init(Foreground::default)
}

/// Native Activity callbacks only: WebView input cannot revive a stopped lease.
#[jni::jni_mangle("cc.kronecker.opennexus.MainActivity")]
pub fn sync_foreground_changed<'caller>(
    _environment: EnvUnowned<'caller>,
    _activity: JObject<'caller>,
    active: jni::sys::jboolean,
) {
    if active != 0 {
        foreground().on_start();
    } else {
        foreground().on_stop();
    }
}

#[jni::jni_mangle("cc.kronecker.opennexus.MainActivity")]
pub fn initialize_sync_tls<'caller>(
    mut environment: EnvUnowned<'caller>,
    _activity: JObject<'caller>,
    application_context: JObject<'caller>,
) {
    environment
        .with_env(|env| {
            crate::mobile_sync_secrets::initialize(env, &application_context)?;
            notesagent_host::sync_tls::initialize(env, application_context)
        })
        .resolve::<ThrowRuntimeExAndDefault>();
}
