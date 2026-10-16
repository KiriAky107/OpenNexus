//! Native session bytes never cross a WebView command. JVM globals retain only
//! an application-scoped store; each Rust worker gets its own bounded JNI frame.
use jni::{
    errors::Error as JniError,
    jni_sig, jni_str,
    objects::{JByteArray, JObject, JString},
    refs::Global,
    Env, JValue, JavaVM,
};
use notesagent_host::sync_client::SyncClient;
use std::{fmt, sync::OnceLock};
use zeroize::Zeroizing;

const MAX_SECRET: usize = 64 * 1024;
const NAMESPACE: &str = "opennexus.sync.v1";
static STORE: OnceLock<NativeSecretStore> = OnceLock::new();

#[derive(Debug)]
pub(crate) struct SecretError(pub(crate) &'static str);
impl fmt::Display for SecretError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(self.0)
    }
}
impl std::error::Error for SecretError {}
impl From<JniError> for SecretError {
    fn from(_: JniError) -> Self {
        Self("SYNC_CREDENTIALS_UNAVAILABLE")
    }
}
type Result<T> = std::result::Result<T, SecretError>;

pub(crate) struct CredentialScope(String);
impl CredentialScope {
    pub(crate) fn for_client(client: &SyncClient, account: &str, device: &str) -> Result<Self> {
        if account.is_empty()
            || account.len() > 128
            || account.contains(['\r', '\n', '\0'])
            || device.is_empty()
            || device.len() > 80
            || !device
                .bytes()
                .all(|byte| byte.is_ascii_alphanumeric() || byte == b'-')
        {
            return Err(SecretError("SYNC_CREDENTIALS_INVALID"));
        }
        let aad = serde_json::to_string(&(1, client.endpoint(), account, device))
            .map_err(|_| SecretError("SYNC_CREDENTIALS_INVALID"))?;
        if aad.len() + NAMESPACE.len() + 1 > 2048 {
            return Err(SecretError("SYNC_CREDENTIALS_INVALID"));
        }
        Ok(Self(aad))
    }
}

pub(crate) struct NativeSecretStore {
    vm: JavaVM,
    object: Global<JObject<'static>>,
}

fn safe_code(message: &str) -> SecretError {
    SecretError(match message {
        "SYNC_SESSION_INVALID" => "SYNC_SESSION_INVALID",
        "SYNC_CREDENTIALS_INVALID" => "SYNC_CREDENTIALS_INVALID",
        _ => "SYNC_CREDENTIALS_UNAVAILABLE",
    })
}

// Never format a Java exception, provider message, stack trace or secret into IPC.
fn java_error(env: &mut Env<'_>, error: JniError) -> SecretError {
    if let JniError::CaughtJavaException { msg, .. } = &error {
        return safe_code(msg);
    }
    if matches!(error, JniError::JavaException) {
        let exception = env.exception_occurred();
        env.exception_clear();
        let message = exception.and_then(|value| {
            let value = env
                .call_method(
                    value,
                    jni_str!("getMessage"),
                    jni_sig!("()Ljava/lang/String;"),
                    &[],
                )
                .ok()?
                .l()
                .ok()?;
            env.cast_local::<JString>(value)
                .ok()?
                .try_to_string(env)
                .ok()
        });
        // A failed message lookup must not leave a pending exception on the worker.
        env.exception_clear();
        if let Some(message) = message {
            return safe_code(&message);
        }
    }
    SecretError("SYNC_CREDENTIALS_UNAVAILABLE")
}

fn wipe_array(env: &mut Env<'_>, bytes: &JByteArray<'_>) -> std::result::Result<(), JniError> {
    let length = bytes.len(env)?;
    let zeros = [0_i8; 4096];
    for start in (0..length).step_by(zeros.len()) {
        bytes.set_region(
            env,
            start as i32,
            &zeros[..(length - start).min(zeros.len())],
        )?;
    }
    Ok(())
}

impl NativeSecretStore {
    fn new(
        env: &mut Env<'_>,
        context: &JObject<'_>,
        namespace: Option<&str>,
    ) -> std::result::Result<Self, JniError> {
        let class = env.find_class(jni_str!("cc/kronecker/opennexus/sync/SyncCredentialStore"))?;
        let object = if let Some(namespace) = namespace {
            let namespace = JString::from_str(env, namespace)?;
            env.new_object(
                class,
                jni_sig!("(Landroid/content/Context;Ljava/lang/String;)V"),
                &[JValue::Object(context), JValue::Object(namespace.as_ref())],
            )?
        } else {
            env.new_object(
                class,
                jni_sig!("(Landroid/content/Context;)V"),
                &[JValue::Object(context)],
            )?
        };
        Ok(Self {
            vm: env.get_java_vm()?,
            object: env.new_global_ref(object)?,
        })
    }

    pub(crate) fn global() -> Result<&'static Self> {
        STORE
            .get()
            .ok_or(SecretError("SYNC_CREDENTIALS_UNAVAILABLE"))
    }

    pub(crate) fn put(&self, scope: &CredentialScope, secret: Zeroizing<Vec<u8>>) -> Result<()> {
        if secret.is_empty() || secret.len() > MAX_SECRET {
            return Err(SecretError("SYNC_CREDENTIALS_INVALID"));
        }
        self.vm.attach_current_thread(|env| -> Result<()> {
            let aad = JString::from_str(env, &scope.0)?;
            let input = env.byte_array_from_slice(&secret)?;
            let called = env.call_method(
                self.object.as_obj(),
                jni_str!("put"),
                jni_sig!("(Ljava/lang/String;[B)V"),
                &[JValue::Object(aad.as_ref()), JValue::Object(input.as_ref())],
            );
            let result = called
                .map_err(|error| java_error(env, error))
                .and_then(|value| value.v().map_err(Into::into));
            wipe_array(env, &input)?;
            result
        })
    }

    pub(crate) fn read(&self, scope: &CredentialScope) -> Result<Option<Zeroizing<Vec<u8>>>> {
        self.vm.attach_current_thread(|env| -> Result<_> {
            let aad = JString::from_str(env, &scope.0)?;
            let called = env.call_method(
                self.object.as_obj(),
                jni_str!("read"),
                jni_sig!("(Ljava/lang/String;)[B"),
                &[JValue::Object(aad.as_ref())],
            );
            let object = called.map_err(|error| java_error(env, error))?.l()?;
            if object.is_null() {
                return Ok(None);
            }
            let bytes = env.cast_local::<JByteArray>(object)?;
            let length = bytes.len(env)?;
            if length == 0 || length > MAX_SECRET {
                wipe_array(env, &bytes)?;
                return Err(SecretError("SYNC_SESSION_INVALID"));
            }
            let result = env
                .convert_byte_array(&bytes)
                .map(Zeroizing::new)
                .map_err(SecretError::from);
            wipe_array(env, &bytes)?;
            result.map(Some)
        })
    }

    pub(crate) fn remove(&self, scope: &CredentialScope) -> Result<()> {
        self.vm.attach_current_thread(|env| -> Result<()> {
            let aad = JString::from_str(env, &scope.0)?;
            let called = env.call_method(
                self.object.as_obj(),
                jni_str!("remove"),
                jni_sig!("(Ljava/lang/String;)V"),
                &[JValue::Object(aad.as_ref())],
            );
            called.map_err(|error| java_error(env, error))?.v()?;
            Ok(())
        })
    }
}

pub(crate) fn initialize(
    env: &mut Env<'_>,
    context: &JObject<'_>,
) -> std::result::Result<(), JniError> {
    if NativeSecretStore::global().is_ok() {
        return Ok(());
    }
    let store = NativeSecretStore::new(env, context, None)?;
    let _ = STORE.set(store);
    Ok(())
}

#[cfg(debug_assertions)]
#[jni::jni_mangle("cc.kronecker.opennexus.sync.NativeCredentialProbe")]
pub fn round_trip<'caller>(
    mut environment: jni::EnvUnowned<'caller>,
    _class: jni::objects::JClass<'caller>,
    context: JObject<'caller>,
    namespace: JString<'caller>,
    account: JString<'caller>,
    device: JString<'caller>,
    input: JByteArray<'caller>,
) -> JByteArray<'caller> {
    use jni::errors::ThrowRuntimeExAndDefault;
    environment
        .with_env(|env| -> std::result::Result<_, JniError> {
            let package = env
                .call_method(
                    &context,
                    jni_str!("getPackageName"),
                    jni_sig!("()Ljava/lang/String;"),
                    &[],
                )?
                .l()?;
            let package = env.cast_local::<JString>(package)?.try_to_string(env)?;
            let namespace = namespace.try_to_string(env)?;
            if package != "cc.kronecker.opennexus.dev"
                || !namespace.starts_with("opennexus.sync.test.")
            {
                return Err(JniError::NullPtr(
                    "Native credential probe is restricted to an owned dev test namespace",
                ));
            }
            let account = account.try_to_string(env)?;
            let device = device.try_to_string(env)?;
            let length = input.len(env)?;
            if length == 0 || length > MAX_SECRET {
                wipe_array(env, &input)?;
                return Err(JniError::NullPtr("Invalid synthetic credential size"));
            }
            let secret = Zeroizing::new(env.convert_byte_array(&input)?);
            wipe_array(env, &input)?;
            let store = NativeSecretStore::new(env, &context, Some(&namespace))?;
            notesagent_host::sync_tls::initialize(env, context)?;
            let result = std::thread::spawn(move || -> Result<_> {
                let client = SyncClient::new(
                    "https://owned.invalid",
                    Zeroizing::new(String::new()),
                    false,
                )
                .map_err(|_| SecretError("SYNC_TLS_UNAVAILABLE"))?;
                let scope = CredentialScope::for_client(&client, &account, &device)?;
                let expected = Zeroizing::new(secret.to_vec());
                store.put(&scope, secret)?;
                let actual = store
                    .read(&scope)?
                    .ok_or(SecretError("SYNC_SESSION_INVALID"))?;
                if *actual != *expected {
                    return Err(SecretError("SYNC_SESSION_INVALID"));
                }
                store.remove(&scope)?;
                if store.read(&scope)?.is_some() {
                    return Err(SecretError("SYNC_SESSION_INVALID"));
                }
                Ok(actual)
            })
            .join()
            .map_err(|_| JniError::NullPtr("Native credential worker failed"))?
            .map_err(|error| JniError::NullPtr(error.0))?;
            env.byte_array_from_slice(&result)
        })
        .resolve::<ThrowRuntimeExAndDefault>()
}
