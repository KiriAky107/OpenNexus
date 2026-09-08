//! Trusted Core process supervisor. The WebView never receives session material.
use command_group::{CommandGroup, GroupChild};
use hmac::{Hmac, Mac};
use serde::Deserialize;
use sha2::Sha256;
use std::collections::VecDeque;
use std::io::{BufRead, BufReader, Read, Write};
use std::path::{Path, PathBuf};
use std::process::{ChildStdin, Command, Stdio};
use std::sync::{mpsc, Arc, Mutex};
use std::time::{Duration, Instant};
use zeroize::Zeroizing;

type Result<T> = std::result::Result<T, String>;
pub type Broker = Arc<dyn Fn(&serde_json::Value) -> Result<serde_json::Value> + Send + Sync>;

/// The manifest is embedded in the Host at build time, never loaded from the installation.
pub fn verify_bundle(root: &Path, manifest: &str) -> Result<()> {
    use sha2::Digest;
    use std::collections::BTreeMap;
    #[derive(Deserialize)]
    struct Manifest {
        protocol: u32,
        product: String,
        files: BTreeMap<String, String>,
    }
    let expected: Manifest = serde_json::from_str(manifest).map_err(|_| "CORE_MANIFEST_INVALID")?;
    if expected.protocol != 1 || expected.product != "OpenNexus" || expected.files.is_empty() {
        return Err("CORE_MANIFEST_INVALID".into());
    }
    fn inventory(
        root: &Path,
        directory: &Path,
        files: &mut BTreeMap<String, String>,
    ) -> Result<()> {
        let metadata = std::fs::symlink_metadata(directory).map_err(|_| "CORE_INTEGRITY_FAILED")?;
        #[cfg(windows)]
        {
            use std::os::windows::fs::MetadataExt;
            if metadata.file_attributes() & 0x400 != 0 {
                return Err("CORE_INTEGRITY_FAILED".into());
            }
        }
        if metadata.file_type().is_symlink() {
            return Err("CORE_INTEGRITY_FAILED".into());
        }
        if metadata.is_dir() {
            for entry in std::fs::read_dir(directory).map_err(|_| "CORE_INTEGRITY_FAILED")? {
                inventory(
                    root,
                    &entry.map_err(|_| "CORE_INTEGRITY_FAILED")?.path(),
                    files,
                )?;
            }
        } else if metadata.is_file() {
            let mut file = std::fs::File::open(directory).map_err(|_| "CORE_INTEGRITY_FAILED")?;
            let mut hash = Sha256::new();
            let mut buffer = [0u8; 65536];
            loop {
                let count = file
                    .read(&mut buffer)
                    .map_err(|_| "CORE_INTEGRITY_FAILED")?;
                if count == 0 {
                    break;
                }
                hash.update(&buffer[..count]);
            }
            let name = directory
                .strip_prefix(root)
                .map_err(|_| "CORE_INTEGRITY_FAILED")?
                .to_str()
                .ok_or("CORE_INTEGRITY_FAILED")?
                .replace('\\', "/");
            files.insert(name, format!("{:x}", hash.finalize()));
        } else {
            return Err("CORE_INTEGRITY_FAILED".into());
        }
        Ok(())
    }
    let mut actual = BTreeMap::new();
    inventory(root, root, &mut actual)?;
    if actual != expected.files {
        return Err("CORE_INTEGRITY_FAILED".into());
    }
    Ok(())
}

fn random_hex() -> Result<String> {
    use rand::RngCore;
    let mut bytes = [0u8; 32];
    rand::rngs::OsRng
        .try_fill_bytes(&mut bytes)
        .map_err(|_| "CORE_ENTROPY_UNAVAILABLE")?;
    Ok(bytes.iter().map(|b| format!("{b:02x}")).collect())
}

fn decode_hex(value: &str) -> Result<Vec<u8>> {
    if value.len() != 64 || !value.bytes().all(|c| c.is_ascii_hexdigit()) {
        return Err("CORE_HANDSHAKE_INVALID".into());
    }
    (0..64)
        .step_by(2)
        .map(|i| {
            u8::from_str_radix(&value[i..i + 2], 16).map_err(|_| "CORE_HANDSHAKE_INVALID".into())
        })
        .collect()
}

#[derive(Deserialize)]
struct Ready {
    protocol: u32,
    pid: u32,
    launcher_pid: u32,
    port: u16,
    generation: String,
    proof: String,
}

fn verify_ready(
    line: &[u8],
    secret: &str,
    challenge: &str,
    generation: &str,
    pid: u32,
) -> Result<u16> {
    let ready: Ready = serde_json::from_slice(line).map_err(|_| "CORE_HANDSHAKE_INVALID")?;
    if ready.protocol != 1 {
        return Err("PROTOCOL_INCOMPATIBLE".into());
    }
    if ready.launcher_pid != pid
        || ready.pid == 0
        || ready.port == 0
        || ready.generation != generation
    {
        return Err("CORE_HANDSHAKE_INVALID".into());
    }
    let mut mac = Hmac::<Sha256>::new_from_slice(&decode_hex(secret)?)
        .map_err(|_| "CORE_HANDSHAKE_INVALID")?;
    mac.update(
        format!(
            "1:{challenge}:{generation}:{pid}:{}:{}",
            ready.pid, ready.port
        )
        .as_bytes(),
    );
    mac.verify_slice(&decode_hex(&ready.proof)?)
        .map_err(|_| "CORE_HANDSHAKE_INVALID")?;
    Ok(ready.port)
}

pub fn checked_url(port: u16, path: &str) -> Result<String> {
    let resource = path.split('?').next().unwrap_or("");
    if !(resource.starts_with("/api/") || resource == "/api" || resource == "/health")
        || path.contains(['\\', '\r', '\n', '#'])
        || resource.contains('%')
        || resource.split('/').any(|part| part == "." || part == "..")
        || resource.contains("//")
        || path.len() > 8192
    {
        return Err("CORE_PATH_DENIED".into());
    }
    Ok(format!("http://127.0.0.1:{port}{path}"))
}

pub struct Session {
    child: GroupChild,
    lifetime: Arc<Mutex<Option<ChildStdin>>>,
    secret: Zeroizing<String>,
    generation: String,
    port: u16,
}

impl Drop for Session {
    fn drop(&mut self) {
        if let Ok(mut pipe) = self.lifetime.lock() {
            pipe.take();
        }
        let deadline = Instant::now() + Duration::from_secs(5);
        while Instant::now() < deadline {
            if self.child.try_wait().ok().flatten().is_some() {
                break;
            }
            std::thread::sleep(Duration::from_millis(25));
        }
        // Kill the entire group even if its leader has exited.
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

pub struct CoreSupervisor {
    executable: PathBuf,
    arguments: Vec<String>,
    working_dir: PathBuf,
    data_dir: PathBuf,
    session: Option<Session>,
    attempts: VecDeque<Instant>,
    next_attempt: Option<Instant>,
    broker: Option<Broker>,
    bundle_manifest: Option<String>,
}

/// Host-only request context; deliberately neither Serialize nor Debug.
pub struct RequestSession {
    pub url: String,
    pub authorization: Zeroizing<String>,
    pub generation: String,
}

impl CoreSupervisor {
    pub fn new(
        executable: PathBuf,
        arguments: Vec<String>,
        working_dir: PathBuf,
        data_dir: PathBuf,
    ) -> Self {
        Self {
            executable,
            arguments,
            working_dir,
            data_dir,
            session: None,
            attempts: VecDeque::new(),
            next_attempt: None,
            broker: None,
            bundle_manifest: None,
        }
    }

    pub fn with_broker(mut self, broker: Broker) -> Self {
        self.broker = Some(broker);
        self
    }

    pub fn with_bundle_manifest(mut self, manifest: String) -> Self {
        self.bundle_manifest = Some(manifest);
        self
    }

    pub fn available(&mut self) -> bool {
        self.session
            .as_mut()
            .is_some_and(|s| matches!(s.child.try_wait(), Ok(None)))
    }

    pub fn request_session(&mut self, path: &str) -> Result<RequestSession> {
        checked_url(1, path)?;
        if !self.available() {
            self.start()?;
        }
        let session = self.session.as_ref().ok_or("CORE_UNAVAILABLE")?;
        Ok(RequestSession {
            url: checked_url(session.port, path)?,
            authorization: Zeroizing::new(format!("Bearer {}", session.secret.as_str())),
            generation: session.generation.clone(),
        })
    }

    pub fn start(&mut self) -> Result<()> {
        if self.available() {
            return Ok(());
        }
        self.session.take();
        let now = Instant::now();
        self.attempts
            .retain(|t| now.duration_since(*t) < Duration::from_secs(300));
        if self.attempts.len() >= 5 {
            return Err("CORE_RESTART_LIMIT".into());
        }
        if self.next_attempt.is_some_and(|t| now < t) {
            return Err("CORE_RESTART_BACKOFF".into());
        }
        self.attempts.push_back(now);
        self.next_attempt = Some(now + Duration::from_secs(1 << (self.attempts.len() - 1)));
        if let Some(manifest) = &self.bundle_manifest {
            verify_bundle(&self.working_dir, manifest)?;
        }
        let session = Self::spawn(
            &self.executable,
            &self.arguments,
            &self.working_dir,
            &self.data_dir,
            self.broker.clone(),
        )?;
        self.session = Some(session);
        Ok(())
    }

    fn spawn(
        executable: &Path,
        args: &[String],
        working_dir: &Path,
        data_dir: &Path,
        broker: Option<Broker>,
    ) -> Result<Session> {
        let secret = Zeroizing::new(random_hex()?);
        let generation = random_hex()?;
        let challenge = random_hex()?;
        let mut command = Command::new(executable);
        command
            .args(args)
            .current_dir(working_dir)
            .env_clear()
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null());
        // Runtime requirements only; never copy Provider tokens or general PATH.
        for key in [
            "SystemRoot",
            "WINDIR",
            "TEMP",
            "TMP",
            "LANG",
            "HOME",
            "USERPROFILE",
            "LOCALAPPDATA",
        ] {
            if let Some(value) = std::env::var_os(key) {
                command.env(key, value);
            }
        }
        command.env("PYTHONUTF8", "1").env("PYTHONUNBUFFERED", "1");
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            command.creation_flags(0x08000000); // CREATE_NO_WINDOW
        }
        let child = command.group_spawn().map_err(|_| "CORE_SPAWN_FAILED")?;
        let mut session = Session {
            child,
            lifetime: Arc::new(Mutex::new(None)),
            secret,
            generation,
            port: 0,
        };
        let stdout = session
            .child
            .inner()
            .stdout
            .take()
            .ok_or("CORE_PIPE_FAILED")?;
        *session.lifetime.lock().map_err(|_| "CORE_PIPE_FAILED")? =
            session.child.inner().stdin.take();
        let mut payload = Zeroizing::new(
            serde_json::to_vec(&serde_json::json!({
                "protocol": 1, "launcher_pid": session.child.id(), "secret": session.secret.as_str(), "challenge": challenge,
                "generation": session.generation, "data_dir": data_dir,
            }))
            .map_err(|_| "CORE_BOOTSTRAP_INVALID")?,
        );
        payload.push(b'\n');
        session
            .lifetime
            .lock()
            .map_err(|_| "CORE_PIPE_FAILED")?
            .as_mut()
            .ok_or("CORE_PIPE_FAILED")?
            .write_all(&payload)
            .map_err(|_| "CORE_PIPE_FAILED")?;
        let (tx, rx) = mpsc::channel();
        let lifetime = session.lifetime.clone();
        std::thread::spawn(move || {
            let mut reader = BufReader::new(stdout);
            loop {
                let mut line = Zeroizing::new(Vec::new());
                match reader
                    .by_ref()
                    .take(8 * 1024 * 1024 + 1)
                    .read_until(b'\n', &mut line)
                {
                    Ok(0) | Err(_) => break,
                    _ => {}
                }
                if line.len() > 8 * 1024 * 1024 {
                    break;
                }
                let Ok(message) = serde_json::from_slice::<serde_json::Value>(&line) else {
                    break;
                };
                if message.get("rpc").is_none() {
                    let _ = tx.send(Ok::<Vec<u8>, std::io::Error>(line.to_vec()));
                    continue;
                }
                let request_id = message
                    .get("request_id")
                    .and_then(|v| v.as_str())
                    .unwrap_or("");
                let result = broker
                    .as_ref()
                    .ok_or_else(|| "HOST_BROKER_UNAVAILABLE".to_string())
                    .and_then(|b| b(&message));
                let response = match result {
                    Ok(result) => serde_json::json!({"request_id":request_id,"result":result}),
                    Err(error) => serde_json::json!({"request_id":request_id,"error":error}),
                };
                let Ok(bytes) = serde_json::to_vec(&response) else {
                    break;
                };
                let mut bytes = Zeroizing::new(bytes);
                if bytes.len() > 8 * 1024 * 1024 {
                    break;
                }
                bytes.push(b'\n');
                let Ok(mut pipe) = lifetime.lock() else {
                    break;
                };
                let Some(pipe) = pipe.as_mut() else {
                    break;
                };
                if pipe.write_all(&bytes).is_err() {
                    break;
                }
            }
        });
        let line = rx
            .recv_timeout(Duration::from_secs(30))
            .map_err(|_| "CORE_READY_TIMEOUT")?
            .map_err(|_| "CORE_HANDSHAKE_INVALID")?;
        if line.len() > 16384 {
            return Err("CORE_HANDSHAKE_INVALID".into());
        }
        session.port = verify_ready(
            &line,
            &session.secret,
            &challenge,
            &session.generation,
            session.child.id(),
        )?;
        Ok(session)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn paths_cannot_redirect_or_escape() {
        for path in [
            "https://evil/api",
            "/api/../x",
            "/api/%2e%2e/x",
            "/api//x",
            "/api/a\\b",
            "/api/a#x",
            "/api/a\r\nHost:x",
        ] {
            assert!(checked_url(4321, path).is_err(), "{path}");
        }
        assert_eq!(
            checked_url(4321, "/api/search?q=%E4%B8%AD").unwrap(),
            "http://127.0.0.1:4321/api/search?q=%E4%B8%AD"
        );
    }
    #[test]
    fn proof_has_python_compatible_framing_and_binds_identity() {
        let secret = "01".repeat(32);
        let challenge = "02".repeat(32);
        let generation = "03".repeat(32);
        let mut mac = Hmac::<Sha256>::new_from_slice(&[1; 32]).unwrap();
        mac.update(format!("1:{challenge}:{generation}:123:123:4567").as_bytes());
        let signature: String = mac
            .finalize()
            .into_bytes()
            .iter()
            .map(|b| format!("{b:02x}"))
            .collect();
        let payload = serde_json::to_vec(&serde_json::json!({"protocol":1,"launcher_pid":123,"pid":123,"port":4567,"generation":generation,"proof":signature})).unwrap();
        assert_eq!(
            verify_ready(&payload, &secret, &challenge, &generation, 123).unwrap(),
            4567
        );
        assert!(verify_ready(&payload, &secret, &challenge, &generation, 124).is_err());
        assert!(verify_ready(&payload, &secret, &"04".repeat(32), &generation, 123).is_err());
    }
}
