//! Platform TLS for the shared Sync transport. The Android host must initialize
//! its JVM application context before opening any client, including test clients.

#[cfg(not(target_os = "android"))]
pub(crate) fn configure(
    builder: reqwest::ClientBuilder,
) -> Result<reqwest::ClientBuilder, crate::sync_client::SyncError> {
    // Keep the existing desktop native-root behavior.
    Ok(builder)
}

#[cfg(target_os = "android")]
mod android {
    use crate::sync_client::SyncError;
    use rustls_platform_verifier::BuilderVerifierExt;
    use std::sync::{
        atomic::{AtomicBool, Ordering},
        Arc,
    };

    static INITIALIZED: AtomicBool = AtomicBool::new(false);

    /// Called only by the native host with its application context. No network
    /// request or token is involved, and repeated Activity creation is safe.
    pub fn initialize(
        env: &mut jni::Env<'_>,
        application_context: jni::objects::JObject<'_>,
    ) -> Result<(), jni::errors::Error> {
        rustls_platform_verifier::android::init_with_env(env, application_context)?;
        INITIALIZED.store(true, Ordering::Release);
        Ok(())
    }

    pub(crate) fn configure(
        builder: reqwest::ClientBuilder,
    ) -> Result<reqwest::ClientBuilder, SyncError> {
        if !INITIALIZED.load(Ordering::Acquire) {
            return Err(SyncError::new("SYNC_TLS_UNAVAILABLE"));
        }
        // Select the same provider explicitly even if another native library has
        // initialized a different process default. TrustManager validates the
        // certificate chain and hostname; there is no Unix CA or plaintext fallback.
        let config = rustls::ClientConfig::builder_with_provider(Arc::new(
            rustls::crypto::ring::default_provider(),
        ))
        .with_safe_default_protocol_versions()
        .map_err(|_| SyncError::new("SYNC_TLS_UNAVAILABLE"))?
        .with_platform_verifier()
        .map_err(|_| SyncError::new("SYNC_TLS_UNAVAILABLE"))?
        .with_no_client_auth();
        Ok(builder.use_preconfigured_tls(config))
    }
}

#[cfg(target_os = "android")]
pub(crate) use android::configure;
#[cfg(target_os = "android")]
pub use android::initialize;
