//! Native startup bridge; it is deliberately not a WebView command.
use jni::{errors::ThrowRuntimeExAndDefault, objects::JObject, EnvUnowned};

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
