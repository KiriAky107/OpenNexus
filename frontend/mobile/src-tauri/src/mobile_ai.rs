//! Android adapter for the shared Rust AI Core. Only public profile metadata and
//! model text cross IPC; decrypted credentials stay inside this module.
use crate::mobile_sync_secrets::{CredentialScope, NativeSecretStore};
use notesagent_host::native_ai::{Analysis, Profile, Runtime, MAX_REQUEST};
use serde::{Deserialize, Serialize};
use serde_json::json;
use std::sync::OnceLock;
use zeroize::{Zeroize, Zeroizing};

static RUNTIME: OnceLock<Runtime> = OnceLock::new();
fn runtime() -> &'static Runtime {
    RUNTIME.get_or_init(Runtime::default)
}
#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Stored {
    schema: u8,
    profile: Profile,
    secret: String,
}
impl Drop for Stored {
    fn drop(&mut self) {
        self.secret.zeroize();
    }
}
#[derive(Deserialize)]
#[serde(tag = "action", rename_all = "snake_case", deny_unknown_fields)]
enum Request {
    Current,
    Configure {
        profile: Profile,
        secret: Option<String>,
    },
    Remove,
    Analyze {
        operation_id: String,
        input: Analysis,
    },
    Cancel {
        operation_id: String,
    },
}
fn load() -> Result<Option<Stored>, &'static str> {
    let store = NativeSecretStore::ai().map_err(|_| "AI_CREDENTIALS_UNAVAILABLE")?;
    let Some(raw) = store
        .read(&CredentialScope::ai_profile())
        .map_err(|_| "AI_CREDENTIALS_UNAVAILABLE")?
    else {
        return Ok(None);
    };
    let record: Stored = serde_json::from_slice(&raw).map_err(|_| "AI_CREDENTIALS_INVALID")?;
    if record.schema != 1 {
        return Err("AI_CREDENTIALS_INVALID");
    }
    record.profile.validate()?;
    Ok(Some(record))
}
fn current() -> Result<String, &'static str> {
    let record = load()?;
    Ok(json!({"profile":record.as_ref().map(|r|&r.profile),"credential_configured":record.as_ref().is_some_and(|r|!r.secret.is_empty()),"native":true,"tasks":["recognize","summarize","organize","generate"]}).to_string())
}
pub async fn dispatch(raw: String) -> Result<String, String> {
    if raw.len() > MAX_REQUEST {
        return Err("AI_INPUT_TOO_LARGE".into());
    }
    let raw = Zeroizing::new(raw);
    let request: Request = serde_json::from_str(&raw).map_err(|_| "AI_REQUEST_INVALID")?;
    let result: Result<String, &'static str> = async {
        match request {
            Request::Cancel { operation_id } => {
                runtime().cancel(&operation_id)?;
                Ok(json!({"cancel_requested":true}).to_string())
            }
            Request::Current => current(),
            Request::Configure { profile, secret } => {
                let _gate = runtime().settings_guard()?;
                crate::mobile_sync_tls::foreground()
                    .lease()
                    .map_err(|_| "AI_FOREGROUND_REQUIRED")?;
                profile.validate()?;
                let old = load()?;
                let secret = match secret {
                    Some(value) => Zeroizing::new(value),
                    None if profile.protocol == "ollama" => Zeroizing::new(String::new()),
                    None => {
                        let old = old.as_ref().ok_or("AI_CREDENTIAL_REQUIRED")?;
                        if old.profile.endpoint != profile.endpoint
                            || old.profile.protocol != profile.protocol
                        {
                            return Err("AI_CREDENTIAL_REQUIRED");
                        }
                        Zeroizing::new(old.secret.clone())
                    }
                };
                if secret.len() > 8192 || secret.chars().any(char::is_control) {
                    return Err("AI_CREDENTIAL_INVALID");
                }
                if secret.is_empty() && profile.protocol != "ollama" {
                    return Err("AI_CREDENTIAL_REQUIRED");
                }
                let stored = Stored {
                    schema: 1,
                    profile,
                    secret: secret.to_string(),
                };
                let buffer =
                    Zeroizing::new(serde_json::to_vec(&stored).map_err(|_| "AI_PROFILE_INVALID")?);
                NativeSecretStore::ai()
                    .map_err(|_| "AI_CREDENTIALS_UNAVAILABLE")?
                    .put(&CredentialScope::ai_profile(), buffer)
                    .map_err(|_| "AI_CREDENTIALS_UNAVAILABLE")?;
                current()
            }
            Request::Remove => {
                let _gate = runtime().settings_guard()?;
                NativeSecretStore::ai()
                    .map_err(|_| "AI_CREDENTIALS_UNAVAILABLE")?
                    .remove(&CredentialScope::ai_profile())
                    .map_err(|_| "AI_CREDENTIALS_UNAVAILABLE")?;
                current()
            }
            Request::Analyze {
                operation_id,
                input,
            } => {
                let lease = crate::mobile_sync_tls::foreground()
                    .lease()
                    .map_err(|_| "AI_FOREGROUND_REQUIRED")?;
                let text = runtime()
                    .run(
                        operation_id,
                        || {
                            let stored = load()?.ok_or("AI_PROFILE_REQUIRED")?;
                            Ok((
                                stored.profile.clone(),
                                Zeroizing::new(stored.secret.clone()),
                            ))
                        },
                        input,
                        lease,
                    )
                    .await?;
                Ok(json!({"text":text,"persisted":false}).to_string())
            }
        }
    }
    .await;
    result.map_err(str::to_owned)
}
