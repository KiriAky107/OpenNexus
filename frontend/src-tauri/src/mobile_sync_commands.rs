//! Bounded native authentication command contract. Responses contain metadata
//! only; replaying a submitted command reads its original receipt and cannot
//! authorize another HTTP request. The Android Host owns the async gate.
use crate::{
    mobile_sync_auth::{Auth, Existing, Login, SecretStore, State, Transport},
    mobile_sync_foreground::Lease,
    mobile_sync_session::{Choice, Profiles, Scope, Selection},
    sync_client::SyncError,
};
use serde::{Deserialize, Deserializer, Serialize};
use zeroize::Zeroizing;

type Result<T> = std::result::Result<T, SyncError>;

// Deliberately no Debug, Clone or Serialize. Even rejected or queued requests
// own zeroizing native password storage and errors never format its value.
pub struct Password(Zeroizing<String>);
impl<'de> Deserialize<'de> for Password {
    fn deserialize<D: Deserializer<'de>>(decoder: D) -> std::result::Result<Self, D::Error> {
        let value = Zeroizing::new(String::deserialize(decoder)?);
        if value.len() > 1024 || !(12..=256).contains(&value.chars().count()) {
            return Err(serde::de::Error::custom("SYNC_LOGIN_INVALID"));
        }
        Ok(Self(value))
    }
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct LoginRequest {
    pub operation_id: String,
    pub endpoint: String,
    pub account: String,
    pub device_name: String,
    pub password: Password,
    pub expected: Option<Selection>,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ExistingRequest {
    pub operation_id: String,
    pub previous_operation: String,
    pub endpoint: String,
    pub account: String,
    pub expected: Selection,
}
#[derive(Deserialize)]
#[serde(tag = "action", rename_all = "snake_case", deny_unknown_fields)]
pub enum Request {
    Current,
    Cancel,
    Profiles {
        offset: usize,
        limit: usize,
    },
    Operation {
        endpoint: String,
        account: String,
        operation_id: String,
    },
    ChoiceReceipt {
        operation_id: String,
    },
    Choose {
        operation_id: String,
        target_operation: String,
        generation: i64,
        expected_revision: Option<i64>,
    },
    Login(LoginRequest),
    Refresh(ExistingRequest),
    Logout(ExistingRequest),
    Disconnect(ExistingRequest),
    ResumeErasure {
        operation_id: String,
        endpoint: String,
        account: String,
        expected: Selection,
    },
}
impl Request {
    /// Called before the Host waits for its async gate, not after acquiring it.
    pub fn invalidates_identity(&self) -> bool {
        matches!(
            self,
            Self::Cancel
                | Self::Choose { .. }
                | Self::Login(_)
                | Self::Refresh(_)
                | Self::Logout(_)
                | Self::Disconnect(_)
                | Self::ResumeErasure { .. }
        )
    }
}

#[derive(Serialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum Response {
    Cancelled,
    Current {
        selection: Option<Selection>,
        state: State,
    },
    Profiles {
        profiles: Profiles,
    },
    Operation {
        state: State,
    },
    Choice {
        choice: Option<Choice>,
    },
}

fn expected<S: SecretStore>(
    auth: &Auth<S>,
    lease: &Lease,
    frozen: Option<&Selection>,
) -> Result<()> {
    if auth.selection(lease)?.as_ref() != frozen {
        return Err(SyncError::new("SYNC_AUTH_SELECTION_CHANGED"));
    }
    Ok(())
}
fn original<S: SecretStore>(
    auth: &Auth<S>,
    lease: &Lease,
    scope: &Scope,
    id: &str,
    now: u64,
) -> Result<Option<State>> {
    match auth.operation(lease, scope, id, now) {
        Ok(state) => Ok(Some(state)),
        Err(error) if error.code == "SYNC_AUTH_INTENT_NOT_FOUND" => Ok(None),
        Err(error) => Err(error),
    }
}
fn existing_matches(state: &State, request: &ExistingRequest, kind: &str) -> Result<()> {
    if state.intent.as_ref().is_none_or(|intent| {
        intent.kind != kind
            || intent.previous_operation.as_deref() != Some(request.previous_operation.as_str())
    }) {
        return Err(SyncError::new("SYNC_AUTH_INTENT_MISMATCH"));
    }
    Ok(())
}

pub async fn execute<S: SecretStore>(
    auth: &mut Auth<S>,
    lease: &Lease,
    request: Request,
    transport: &impl Transport,
    now: u64,
) -> Result<Response> {
    lease.check()?;
    let reply = match request {
        // Native Host invalidated before waiting for the gate. Cancelling a
        // view does not decrypt, change selected profiles or reconcile an
        // uncertain response. The next explicit Current reads that state.
        Request::Cancel => Response::Cancelled,
        Request::Current => {
            let state = auth.recover_selected(lease, now)?;
            Response::Current {
                selection: auth.selection(lease)?,
                state,
            }
        }
        Request::Profiles { offset, limit } => Response::Profiles {
            profiles: auth.profiles(lease, offset, limit)?,
        },
        Request::Operation {
            endpoint,
            account,
            operation_id,
        } => Response::Operation {
            state: auth.operation(lease, &Scope::new(&endpoint, &account)?, &operation_id, now)?,
        },
        Request::ChoiceReceipt { operation_id } => Response::Choice {
            choice: auth.choice(lease, &operation_id)?,
        },
        Request::Choose {
            operation_id,
            target_operation,
            generation,
            expected_revision,
        } => Response::Choice {
            choice: Some(auth.choose_profile(
                lease,
                &operation_id,
                &target_operation,
                generation,
                expected_revision,
            )?),
        },
        Request::Login(request) => {
            let scope = Scope::new(&request.endpoint, &request.account)?;
            if let Some(state) = original(auth, lease, &scope, &request.operation_id, now)? {
                if state.intent.as_ref().is_none_or(|intent| {
                    intent.kind != "login" || intent.device_name != request.device_name
                }) {
                    return Err(SyncError::new("SYNC_AUTH_INTENT_MISMATCH"));
                }
                // Password is dropped here. A historical receipt is never a
                // currently authenticated connection or a selection change.
                Response::Operation { state }
            } else {
                expected(auth, lease, request.expected.as_ref())?;
                let state = auth
                    .login(
                        lease,
                        Login {
                            operation_id: &request.operation_id,
                            scope: &scope,
                            device_name: &request.device_name,
                            password: request.password.0,
                        },
                        transport,
                        now,
                    )
                    .await?;
                Response::Current {
                    selection: auth.selection(lease)?,
                    state,
                }
            }
        }
        Request::Refresh(request) => {
            execute_existing(auth, lease, request, "refresh", transport, now).await?
        }
        Request::Logout(request) => {
            execute_existing(auth, lease, request, "logout", transport, now).await?
        }
        Request::Disconnect(request) => {
            execute_existing(auth, lease, request, "local_disconnect", transport, now).await?
        }
        Request::ResumeErasure {
            operation_id,
            endpoint,
            account,
            expected: frozen,
        } => {
            expected(auth, lease, Some(&frozen))?;
            let state =
                auth.resume_erasure(lease, &Scope::new(&endpoint, &account)?, &operation_id, now)?;
            Response::Current {
                selection: auth.selection(lease)?,
                state,
            }
        }
    };
    lease.with_current(|| Ok(reply))
}

async fn execute_existing<S: SecretStore>(
    auth: &mut Auth<S>,
    lease: &Lease,
    request: ExistingRequest,
    kind: &str,
    transport: &impl Transport,
    now: u64,
) -> Result<Response> {
    let scope = Scope::new(&request.endpoint, &request.account)?;
    if let Some(state) = original(auth, lease, &scope, &request.operation_id, now)? {
        existing_matches(&state, &request, kind)?;
        return Ok(Response::Operation { state });
    }
    expected(auth, lease, Some(&request.expected))?;
    let input = Existing {
        operation_id: &request.operation_id,
        previous_operation: &request.previous_operation,
        scope: &scope,
    };
    let state = match kind {
        "refresh" => auth.refresh(lease, input, transport, now).await?,
        "logout" => auth.logout(lease, input, transport, now).await?,
        "local_disconnect" => auth.local_disconnect(lease, input, now)?,
        _ => return Err(SyncError::new("SYNC_AUTH_INTENT_INVALID")),
    };
    Ok(Response::Current {
        selection: auth.selection(lease)?,
        state,
    })
}

/// Never forward a server/provider message, arbitrary response code or path.
pub fn public_error(error: SyncError) -> String {
    match error.code.as_str() {
        "SYNC_AUTH_IDENTITY_CHANGED"
        | "SYNC_AUTH_INDEX_INVALID"
        | "SYNC_AUTH_INTENT_INVALID"
        | "SYNC_AUTH_INTENT_MISMATCH"
        | "SYNC_AUTH_INTENT_NOT_FOUND"
        | "SYNC_AUTH_PROFILE_PAGE_INVALID"
        | "SYNC_AUTH_RECONCILIATION_REQUIRED"
        | "SYNC_AUTH_RESPONSE_MISMATCH"
        | "SYNC_AUTH_SELECTION_CHANGED"
        | "SYNC_AUTH_SELECTION_INVALID"
        | "SYNC_CREDENTIAL_CHANGE_REQUIRED"
        | "SYNC_CREDENTIALS_INVALID"
        | "SYNC_CREDENTIALS_UNAVAILABLE"
        | "SYNC_ENDPOINT_INVALID"
        | "SYNC_FOREGROUND_CHANGED"
        | "SYNC_FOREGROUND_REQUIRED"
        | "SYNC_LOGIN_INVALID"
        | "SYNC_LOGIN_REQUIRED"
        | "SYNC_NETWORK_ERROR"
        | "SYNC_REDIRECT_BLOCKED"
        | "SYNC_RESPONSE_INVALID"
        | "SYNC_RESPONSE_TOO_LARGE"
        | "SYNC_SESSION_EXPIRED"
        | "SYNC_SESSION_INVALID"
        | "SYNC_TIME_INVALID"
        | "SYNC_TLS_UNAVAILABLE"
        | "LOGIN_FAILED"
        | "RATE_LIMITED"
        | "INVALID_REQUEST"
        | "SESSION_EXPIRED" => error.code,
        _ => "SYNC_CONNECTION_FAILED".into(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn password_and_command_schema_reject_unknown_fields_without_echoing_sensitive_values() {
        let password = "private-too-short";
        let invalid = serde_json::json!({"action":"current","password":password});
        assert!(serde_json::from_value::<Request>(invalid).is_err());
        let input = serde_json::json!({"action":"login","operation_id":uuid::Uuid::new_v4().to_string(),
            "endpoint":"https://sync.example","account":"reader","device_name":"Owned phone",
            "password":"sensitive","expected":null});
        let error = serde_json::from_value::<Request>(input)
            .err()
            .unwrap()
            .to_string();
        assert!(error.contains("SYNC_LOGIN_INVALID"));
        assert!(!error.contains("sensitive"));
        let excess = serde_json::json!({"action":"login","operation_id":uuid::Uuid::new_v4().to_string(),
            "endpoint":"https://sync.example","account":"reader","device_name":"Owned phone",
            "password":"私".repeat(257),"expected":null});
        assert!(serde_json::from_value::<Request>(excess).is_err());
    }
    #[test]
    fn arbitrary_provider_error_content_is_never_an_ipc_error() {
        for code in [
            "secret-token-and-path",
            "SYNC_CREDENTIALS_UNAVAILABLE secret",
            "<script>original</script>",
        ] {
            assert_eq!(public_error(SyncError::new(code)), "SYNC_CONNECTION_FAILED");
        }
        assert_eq!(
            public_error(SyncError::new("SYNC_AUTH_SELECTION_CHANGED")),
            "SYNC_AUTH_SELECTION_CHANGED"
        );
        assert_eq!(public_error(SyncError::new("LOGIN_FAILED")), "LOGIN_FAILED");
    }
}
