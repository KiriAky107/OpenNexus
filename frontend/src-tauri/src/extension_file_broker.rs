//! 实例绑定文件 RPC 策略。 Transport 必须将一个 Broker 绑定到一个经过身份验证的实例。写入提交当前使用 Workspace 的事务；在不受信任的激活之前，需要完全的句柄相关的写强化。
use crate::{
    credentials::CredentialBroker,
    extension_permit::{Authority, Claims, Lease, Permit},
    workspace::{HostError, Result, Workspace},
};
use cap_fs_ext::{DirExt, FollowSymlinks, OpenOptionsFollowExt};
use cap_std::fs::{Dir, OpenOptions, OpenOptionsExt};
use serde::Deserialize;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::{
    collections::BTreeSet,
    io::Read,
    os::windows::io::AsRawHandle,
    time::{Duration, Instant},
};
use windows_sys::Win32::Storage::FileSystem::*;

pub use crate::extension_stdio::MAX_FRAME_BYTES;
pub const MAX_NOTE_BYTES: usize = 1024 * 1024;
const REQUESTS_PER_SECOND: u32 = 32;
#[derive(Deserialize)]
#[serde(tag = "method", deny_unknown_fields)]
enum Request {
    #[serde(rename = "notes.read")]
    Read { path: String },
    #[serde(rename = "notes.write")]
    Write {
        path: String,
        expected_hash: String,
        content: String,
        operation_id: String,
    },
}
pub struct Broker {
    root: Dir,
    vault: String,
    permissions: BTreeSet<String>,
    lease: Lease,
    identity: Vec<u8>,
    window: Instant,
    requests: u32,
}
impl Broker {
    /// 实例身份绑定后由Host调用；绝不是 IPC 命令。
    pub fn bind(
        authority: &Authority,
        permit: &Permit,
        claims: &Claims,
        credentials: &CredentialBroker,
        workspace: &Workspace,
        policy_version: &str,
        now_ms: u64,
    ) -> Result<Self> {
        let mut lease = authority.lease(permit, claims, now_ms)?;
        lease.bind_credential(credentials.lock_signal());
        if credentials.is_locked() {
            return Err(HostError::new("CREDENTIALS_LOCKED"));
        }
        if claims.platform != std::env::consts::OS || claims.policy_version != policy_version {
            return Err(HostError::new("EXTENSION_EXECUTION_CONTEXT_CHANGED"));
        }
        if claims.vault_id != workspace.vault_id {
            return Err(HostError::new("VAULT_PERMISSION_CHANGED"));
        }
        let root = Dir::open_ambient_dir(&workspace.root, cap_std::ambient_authority())?;
        let identity = serde_json::to_vec(&(
            claims.kind,
            &claims.source,
            &claims.namespace,
            &claims.package_id,
            &claims.vault_id,
        ))
        .map_err(|_| HostError::new("EXTENSION_BROKER_INVALID"))?;
        lease.check()?;
        Ok(Self {
            root,
            vault: claims.vault_id.clone(),
            permissions: claims.permissions.clone(),
            lease,
            identity,
            window: Instant::now(),
            requests: 0,
        })
    }
    fn operation(&self, operation: &str) -> Result<String> {
        let id =
            uuid::Uuid::parse_str(operation).map_err(|_| HostError::new("OPERATION_ID_INVALID"))?;
        if id.to_string() != operation {
            return Err(HostError::new("OPERATION_ID_INVALID"));
        }
        let mut digest = Sha256::new();
        digest.update(b"OpenNexus extension note operation v1\0");
        digest.update((self.identity.len() as u64).to_be_bytes());
        digest.update(&self.identity);
        digest.update(id.as_bytes());
        let mut bytes: [u8; 16] = digest.finalize()[..16].try_into().unwrap();
        bytes[6] = (bytes[6] & 15) | 128;
        bytes[8] = (bytes[8] & 63) | 128;
        Ok(uuid::Uuid::from_bytes(bytes).to_string())
    }
    fn permit(&self, permission: &str) -> Result<()> {
        if !self.permissions.contains(permission) {
            return Err(HostError::new("EXTENSION_PERMISSION_DENIED"));
        }
        self.lease.check()
    }
    /// 传输在分配之前读取时必须应用 MAX_FRAME_BYTES。在整个调度和提交过程中保持 Host 工作区锁。
    pub fn dispatch(&mut self, workspace: &mut Workspace, bytes: &[u8]) -> Result<Value> {
        self.lease.check()?;
        if workspace.vault_id != self.vault {
            return Err(HostError::new("VAULT_PERMISSION_CHANGED"));
        }
        if self.window.elapsed() >= Duration::from_secs(1) {
            self.window = Instant::now();
            self.requests = 0;
        }
        if self.requests >= REQUESTS_PER_SECOND {
            return Err(HostError::new("EXTENSION_BROKER_RATE_LIMITED"));
        }
        self.requests += 1;
        if bytes.len() > MAX_FRAME_BYTES {
            return Err(HostError::new("EXTENSION_BROKER_REQUEST_TOO_LARGE"));
        }
        let request: Request = serde_json::from_slice(bytes)
            .map_err(|_| HostError::new("EXTENSION_BROKER_REQUEST_INVALID"))?;
        self.permit(match &request {
            Request::Read { .. } => "notes.read",
            Request::Write { .. } => "notes.write",
        })?;
        let path = match &request {
            Request::Read { path } | Request::Write { path, .. } => path,
        };
        if path.len() > 1024
            || !path.to_ascii_lowercase().ends_with(".md")
            || path
                .split('/')
                .any(|part| part.is_empty() || part == "." || part == "..")
        {
            return Err(HostError::new("EXTENSION_BROKER_PATH_INVALID"));
        }
        workspace.resolve(path)?;
        match request {
            Request::Read { path } => {
                let mut parent = self.root.try_clone()?;
                let mut parts = path.split('/').peekable();
                let mut leaf = None;
                while let Some(part) = parts.next() {
                    if part.is_empty() || part == "." || part == ".." {
                        return Err(HostError::new("UNSAFE_PATH"));
                    }
                    if parts.peek().is_none() {
                        leaf = Some(part);
                    } else {
                        parent = parent.open_dir_nofollow(part)?;
                    }
                }
                let mut options = OpenOptions::new();
                options
                    .read(true)
                    .follow(FollowSymlinks::No)
                    .share_mode(FILE_SHARE_READ);
                let file = parent
                    .open_with(leaf.ok_or_else(|| HostError::new("UNSAFE_PATH"))?, &options)?
                    .into_std();
                let metadata = file.metadata()?;
                let mut info = BY_HANDLE_FILE_INFORMATION::default();
                if !metadata.is_file()
                    || unsafe { GetFileInformationByHandle(file.as_raw_handle(), &mut info) } == 0
                    || info.nNumberOfLinks != 1
                    || info.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT != 0
                {
                    return Err(HostError::new("UNSAFE_PATH"));
                }
                if metadata.len() > MAX_NOTE_BYTES as u64 {
                    return Err(HostError::new("EXTENSION_NOTE_TOO_LARGE"));
                }
                let mut content = Vec::new();
                file.take(MAX_NOTE_BYTES as u64 + 1)
                    .read_to_end(&mut content)?;
                if content.len() > MAX_NOTE_BYTES {
                    return Err(HostError::new("EXTENSION_NOTE_TOO_LARGE"));
                }
                let content = String::from_utf8(content)
                    .map_err(|_| HostError::new("EXTENSION_NOTE_ENCODING_INVALID"))?;
                self.lease.check()?;
                Ok(
                    json!({"path":path,"expected_hash":format!("{:x}",Sha256::digest(content.as_bytes())),"content":content}),
                )
            }
            Request::Write {
                path,
                expected_hash,
                content,
                operation_id,
            } => {
                if content.len() > MAX_NOTE_BYTES {
                    return Err(HostError::new("EXTENSION_NOTE_TOO_LARGE"));
                }
                if !expected_hash.is_empty()
                    && (expected_hash.len() != 64
                        || !expected_hash
                            .bytes()
                            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b)))
                {
                    return Err(HostError::new("EXTENSION_BROKER_REQUEST_INVALID"));
                }
                let operation = self.operation(&operation_id)?;
                self.lease.check()?;
                let entry = workspace.write_operation_guarded(
                    &path,
                    &expected_hash,
                    content.as_bytes(),
                    &operation,
                    || self.lease.check(),
                )?;
                Ok(json!({"operation_id":operation_id,"state":"committed","result":entry}))
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::extension_permit::ExecutionKind;
    use zeroize::Zeroizing;
    fn setup() -> (
        tempfile::TempDir,
        Workspace,
        CredentialBroker,
        Authority,
        Claims,
    ) {
        let temp = tempfile::tempdir().unwrap();
        let vault = temp.path().join("vault");
        std::fs::create_dir(&vault).unwrap();
        let mut ws = Workspace::open(&vault).unwrap();
        ws.write("note.md", "", b"original", "local").unwrap();
        let mut credentials = CredentialBroker::new(temp.path().join("credentials.v1"));
        credentials
            .unlock(Zeroizing::new(b"file broker fixture password".to_vec()))
            .unwrap();
        let claims = Claims {
            kind: ExecutionKind::Mcp,
            source: "https://catalog.example/".into(),
            namespace: "examples".into(),
            package_id: "reviewer".into(),
            version: "1.0.0".into(),
            archive_sha256: "a".repeat(64),
            tree_sha256: "b".repeat(64),
            signer_sha256: "c".repeat(64),
            entry: "entry.exe".into(),
            arguments: vec![],
            environment: Default::default(),
            permissions: ["notes.read".into(), "notes.write".into()]
                .into_iter()
                .collect(),
            vault_id: ws.vault_id.clone(),
            platform: "windows".into(),
            policy_version: "1".into(),
            expires_at_ms: 120_000,
        };
        (temp, ws, credentials, Authority::default(), claims)
    }
    fn call(broker: &mut Broker, ws: &mut Workspace, request: Value) -> Result<Value> {
        broker.dispatch(ws, &serde_json::to_vec(&request).unwrap())
    }
    #[test]
    fn bound_read_write_cas_and_scoped_operation_replay_use_host_journal() {
        let (_temp, mut ws, credentials, authority, claims) = setup();
        let permit = authority.issue(&claims, 1).unwrap();
        let mut broker =
            Broker::bind(&authority, &permit, &claims, &credentials, &ws, "1", 2).unwrap();
        let read = call(
            &mut broker,
            &mut ws,
            json!({"method":"notes.read","path":"note.md"}),
        )
        .unwrap();
        assert_eq!(read["content"], "original");
        let operation = uuid::Uuid::new_v4().to_string();
        let write = json!({"method":"notes.write","path":"note.md","expected_hash":read["expected_hash"],"content":"extension update","operation_id":operation});
        let receipt = call(&mut broker, &mut ws, write.clone()).unwrap();
        let revision = receipt["result"]["revision"].clone();
        assert_eq!(call(&mut broker, &mut ws, write.clone()).unwrap(), receipt);
        assert_eq!(ws.read("note.md").unwrap().content, "extension update");
        let mut altered = write.clone();
        altered["content"] = json!("changed payload");
        assert_eq!(
            call(&mut broker, &mut ws, altered).unwrap_err().code,
            "OPERATION_PAYLOAD_CONFLICT"
        );
        // 使用真正的新操作 ID 构建过期的 CAS。
        let mut stale = write.clone();
        stale["operation_id"] = json!(uuid::Uuid::new_v4().to_string());
        assert_eq!(
            call(&mut broker, &mut ws, stale).unwrap_err().code,
            "REVISION_CONFLICT"
        );
        let scoped = broker.operation(&operation).unwrap();
        assert!(ws.operation(&operation).unwrap().is_none());
        assert_eq!(
            ws.operation(&scoped).unwrap().unwrap()["result"]["revision"],
            revision
        );
        let mut another = claims.clone();
        another.package_id = "another".into();
        let other_permit = authority.issue(&another, 1).unwrap();
        let other = Broker::bind(
            &authority,
            &other_permit,
            &another,
            &credentials,
            &ws,
            "1",
            2,
        )
        .unwrap();
        assert_ne!(other.operation(&operation).unwrap(), scoped);
        drop(other);
        drop(broker);
        let root = ws.root.clone();
        drop(ws);
        let mut ws = Workspace::open(&root).unwrap();
        let mut replay =
            Broker::bind(&authority, &permit, &claims, &credentials, &ws, "1", 2).unwrap();
        assert_eq!(call(&mut replay, &mut ws, write).unwrap(), receipt);
    }
    #[test]
    fn permissions_scope_limits_hardlinks_and_revocation_fail_closed() {
        let (_temp, mut ws, mut credentials, authority, mut claims) = setup();
        claims.permissions.clear();
        let permit = authority.issue(&claims, 1).unwrap();
        let mut denied =
            Broker::bind(&authority, &permit, &claims, &credentials, &ws, "1", 2).unwrap();
        assert_eq!(
            call(
                &mut denied,
                &mut ws,
                json!({"method":"notes.read","path":"note.md"})
            )
            .unwrap_err()
            .code,
            "EXTENSION_PERMISSION_DENIED"
        );
        claims.permissions.insert("notes.read".into());
        let permit = authority.issue(&claims, 1).unwrap();
        let mut broker =
            Broker::bind(&authority, &permit, &claims, &credentials, &ws, "1", 2).unwrap();
        assert_eq!(
            call(
                &mut broker,
                &mut ws,
                json!({"method":"notes.read","path":"note.md","vault_id":"forged"})
            )
            .unwrap_err()
            .code,
            "EXTENSION_BROKER_REQUEST_INVALID"
        );
        assert_eq!(
            call(
                &mut broker,
                &mut ws,
                json!({"method":"credentials.resolve"})
            )
            .unwrap_err()
            .code,
            "EXTENSION_BROKER_REQUEST_INVALID"
        );
        assert!(call(
            &mut broker,
            &mut ws,
            json!({"method":"notes.read","path":".ainote/private.md"})
        )
        .is_err());
        assert_eq!(
            broker
                .dispatch(&mut ws, &vec![b' '; MAX_FRAME_BYTES + 1])
                .unwrap_err()
                .code,
            "EXTENSION_BROKER_REQUEST_TOO_LARGE"
        );
        std::fs::write(ws.root.join("large.md"), vec![b'x'; MAX_NOTE_BYTES + 1]).unwrap();
        assert_eq!(
            call(
                &mut broker,
                &mut ws,
                json!({"method":"notes.read","path":"large.md"})
            )
            .unwrap_err()
            .code,
            "EXTENSION_NOTE_TOO_LARGE"
        );
        let outside = tempfile::tempdir().unwrap();
        std::fs::hard_link(ws.root.join("note.md"), outside.path().join("alias.md")).unwrap();
        assert_eq!(
            call(
                &mut broker,
                &mut ws,
                json!({"method":"notes.read","path":"note.md"})
            )
            .unwrap_err()
            .code,
            "UNSAFE_PATH"
        );
        let second = tempfile::tempdir().unwrap();
        let mut other = Workspace::open(second.path()).unwrap();
        assert_eq!(
            broker.dispatch(&mut other, b"{}").unwrap_err().code,
            "VAULT_PERMISSION_CHANGED"
        );
        let mut limited =
            Broker::bind(&authority, &permit, &claims, &credentials, &ws, "1", 2).unwrap();
        for _ in 0..REQUESTS_PER_SECOND {
            assert_eq!(
                limited.dispatch(&mut ws, b"{}").unwrap_err().code,
                "EXTENSION_BROKER_REQUEST_INVALID"
            );
        }
        assert_eq!(
            limited.dispatch(&mut ws, b"{}").unwrap_err().code,
            "EXTENSION_BROKER_RATE_LIMITED"
        );
        credentials.lock();
        assert_eq!(
            broker.dispatch(&mut ws, b"{}").unwrap_err().code,
            "CREDENTIALS_LOCKED"
        );
        assert!(Broker::bind(&authority, &permit, &claims, &credentials, &ws, "1", 2).is_err());
    }
}
