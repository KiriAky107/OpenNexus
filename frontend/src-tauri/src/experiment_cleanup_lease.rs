//! An OS-held lease for one Host's entire experiment attempt, across processes.
//! A PID, marker's existence, or SQLite row alone is never a liveness proof.
use crate::workspace::{HostError, Result};
use cap_fs_ext::{FollowSymlinks, OpenOptionsFollowExt};
use cap_std::fs::{Dir, OpenOptions};
use std::{fs::File, path::Path};

fn failed() -> HostError {
    HostError::new("EXPERIMENT_CLEANUP_JOURNAL_FAILED")
}

#[derive(Debug, PartialEq, Eq)]
struct ObjectId(u64, u64);
fn identity(file: &File, directory: bool) -> Result<ObjectId> {
    let metadata = file.metadata().map_err(|_| failed())?;
    if metadata.is_dir() != directory || (!directory && !metadata.is_file()) {
        return Err(failed());
    }
    #[cfg(windows)]
    {
        use std::os::windows::io::AsRawHandle;
        use windows_sys::Win32::Storage::FileSystem::*;
        let mut info = BY_HANDLE_FILE_INFORMATION::default();
        if unsafe { GetFileInformationByHandle(file.as_raw_handle(), &mut info) } == 0
            || info.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT != 0
            || (!directory && info.nNumberOfLinks != 1)
        {
            return Err(failed());
        }
        Ok(ObjectId(
            info.dwVolumeSerialNumber as u64,
            (u64::from(info.nFileIndexHigh) << 32) | u64::from(info.nFileIndexLow),
        ))
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        if !directory && metadata.nlink() != 1 {
            return Err(failed());
        }
        Ok(ObjectId(metadata.dev(), metadata.ino()))
    }
}

pub(crate) struct Gate {
    root: Dir,
    // Windows denies renaming/replacing every held ancestor. Unix operations
    // stay relative to the original root capability even if its name moves.
    _ancestors: Vec<File>,
}
pub(crate) struct Lease {
    file: File,
}
impl Drop for Lease {
    fn drop(&mut self) {
        // Closing is the final fallback if explicit unlocking fails. Never
        // delete the lock file: another process must lock the very same object.
        let _ = fs2::FileExt::unlock(&self.file);
    }
}
impl Gate {
    pub(crate) fn open(root: &Path) -> Result<Self> {
        if !root.is_absolute() {
            return Err(failed());
        }
        let mut ancestors = Vec::new();
        for path in root.ancestors() {
            let mut options = std::fs::OpenOptions::new();
            options.read(true);
            #[cfg(windows)]
            {
                use std::os::windows::fs::OpenOptionsExt;
                use windows_sys::Win32::Storage::FileSystem::*;
                options
                    .access_mode(FILE_READ_ATTRIBUTES)
                    .share_mode(FILE_SHARE_READ | FILE_SHARE_WRITE)
                    .custom_flags(FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT);
            }
            #[cfg(unix)]
            {
                use std::os::unix::fs::OpenOptionsExt;
                options.custom_flags(libc::O_DIRECTORY | libc::O_NOFOLLOW | libc::O_CLOEXEC);
            }
            let file = options.open(path).map_err(|_| failed())?;
            identity(&file, true)?;
            ancestors.push(file);
        }
        let mut options = std::fs::OpenOptions::new();
        options.read(true);
        #[cfg(windows)]
        {
            use std::os::windows::fs::OpenOptionsExt;
            use windows_sys::Win32::Storage::FileSystem::*;
            options
                .share_mode(FILE_SHARE_READ | FILE_SHARE_WRITE)
                .custom_flags(FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT);
        }
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.custom_flags(libc::O_DIRECTORY | libc::O_NOFOLLOW | libc::O_CLOEXEC);
        }
        let file = options.open(root).map_err(|_| failed())?;
        if identity(&file, true)? != identity(&ancestors[0], true)? {
            return Err(failed());
        }
        Ok(Self {
            root: Dir::from_std_file(file),
            _ancestors: ancestors,
        })
    }
    pub(crate) fn acquire(&self) -> Result<Lease> {
        const NAME: &str = "experiment-owner.lock";
        let mut options = OpenOptions::new();
        options
            .read(true)
            .write(true)
            .create(true)
            .truncate(false)
            .follow(FollowSymlinks::No);
        #[cfg(windows)]
        {
            use cap_std::fs::OpenOptionsExt;
            use windows_sys::Win32::Storage::FileSystem::*;
            options
                .share_mode(FILE_SHARE_READ | FILE_SHARE_WRITE)
                .custom_flags(FILE_FLAG_OPEN_REPARSE_POINT);
        }
        #[cfg(unix)]
        {
            use cap_std::fs::OpenOptionsExt;
            options.mode(0o600);
        }
        let file = self
            .root
            .open_with(NAME, &options)
            .map_err(|_| failed())?
            .into_std();
        let expected = identity(&file, false)?;
        fs2::FileExt::try_lock_exclusive(&file)
            .map_err(|_| HostError::new("EXPERIMENT_CLEANUP_OWNER_ACTIVE"))?;
        let lease = Lease { file };
        // Re-open through the held directory, without following links. This
        // also rejects an unlinked/replaced Unix lock before authorizing work.
        options.create(false);
        let current = self
            .root
            .open_with(NAME, &options)
            .map_err(|_| failed())?
            .into_std();
        if identity(&current, false)? != expected {
            return Err(failed());
        }
        Ok(lease)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::{BufRead, Write};

    struct Child(std::process::Child);
    impl Drop for Child {
        fn drop(&mut self) {
            let _ = self.0.kill();
            let _ = self.0.wait();
        }
    }

    #[test]
    #[ignore = "Owned child fixture; launched only by process_death_releases_the_actual_os_lease"]
    fn lease_child_fixture() {
        let root = std::env::var_os("OPENNEXUS_CLEANUP_LEASE_TEST_ROOT").unwrap();
        let gate = Gate::open(Path::new(&root)).unwrap();
        let _lease = gate.acquire().unwrap();
        println!("LEASE_HELD");
        std::io::stdout().flush().unwrap();
        loop {
            std::thread::park();
        }
    }

    #[test]
    fn process_death_releases_the_actual_os_lease() {
        let root = tempfile::tempdir().unwrap();
        let gate = Gate::open(root.path()).unwrap();
        let mut child = Child(
            std::process::Command::new(std::env::current_exe().unwrap())
                .args([
                    "--exact",
                    "experiment_cleanup_lease::tests::lease_child_fixture",
                    "--ignored",
                    "--nocapture",
                ])
                .env("OPENNEXUS_CLEANUP_LEASE_TEST_ROOT", root.path())
                .stdout(std::process::Stdio::piped())
                .spawn()
                .unwrap(),
        );
        let stdout = child.0.stdout.take().unwrap();
        let (send, receive) = std::sync::mpsc::channel();
        let reader = std::thread::spawn(move || {
            for line in std::io::BufReader::new(stdout).lines().take(20) {
                if line.unwrap_or_default() == "LEASE_HELD" {
                    let _ = send.send(());
                    break;
                }
            }
        });
        receive
            .recv_timeout(std::time::Duration::from_secs(10))
            .unwrap();
        assert_eq!(
            gate.acquire().err().unwrap().code,
            "EXPERIMENT_CLEANUP_OWNER_ACTIVE"
        );
        #[cfg(windows)]
        assert!(std::fs::rename(
            root.path().join("experiment-owner.lock"),
            root.path().join("replacement")
        )
        .is_err());
        child.0.kill().unwrap();
        child.0.wait().unwrap();
        reader.join().unwrap();
        let deadline = std::time::Instant::now() + std::time::Duration::from_secs(2);
        let _lease = loop {
            match gate.acquire() {
                Ok(lease) => break lease,
                Err(error)
                    if error.code == "EXPERIMENT_CLEANUP_OWNER_ACTIVE"
                        && std::time::Instant::now() < deadline =>
                {
                    std::thread::sleep(std::time::Duration::from_millis(10))
                }
                Err(error) => panic!(
                    "Lease did not become available after owned child exited: {}",
                    error.code
                ),
            }
        };
        assert_eq!(
            std::fs::metadata(root.path().join("experiment-owner.lock"))
                .unwrap()
                .len(),
            0
        );
    }
    #[test]
    fn connections_share_the_same_lease_and_unlock_without_deleting_it() {
        let root = tempfile::tempdir().unwrap();
        let gate = Gate::open(root.path()).unwrap();
        let other = Gate::open(root.path()).unwrap();
        let lease = gate.acquire().unwrap();
        assert_eq!(
            other.acquire().err().unwrap().code,
            "EXPERIMENT_CLEANUP_OWNER_ACTIVE"
        );
        assert!(root.path().join("experiment-owner.lock").is_file());
        drop(lease);
        let second = other.acquire().unwrap();
        assert!(gate.acquire().is_err());
        drop(second);
        assert!(root.path().join("experiment-owner.lock").is_file());
    }
    #[test]
    fn linked_lock_never_changes_the_external_object() {
        let root = tempfile::tempdir().unwrap();
        let external = tempfile::tempdir().unwrap();
        let target = external.path().join("sentinel");
        std::fs::write(&target, b"preserve").unwrap();
        std::fs::hard_link(&target, root.path().join("experiment-owner.lock")).unwrap();
        let gate = Gate::open(root.path()).unwrap();
        assert!(gate.acquire().is_err());
        assert_eq!(std::fs::read(&target).unwrap(), b"preserve");
    }
    #[test]
    fn linked_root_is_rejected_without_touching_external_files() {
        let parent = tempfile::tempdir().unwrap();
        let external = tempfile::tempdir().unwrap();
        std::fs::write(external.path().join("sentinel"), b"preserve").unwrap();
        let link = parent.path().join("root-link");
        #[cfg(windows)]
        assert!(std::process::Command::new("cmd.exe")
            .args(["/d", "/c", "mklink", "/J"])
            .arg(&link)
            .arg(external.path())
            .output()
            .unwrap()
            .status
            .success());
        #[cfg(unix)]
        std::os::unix::fs::symlink(external.path(), &link).unwrap();
        assert!(Gate::open(&link).is_err());
        assert_eq!(
            std::fs::read(external.path().join("sentinel")).unwrap(),
            b"preserve"
        );
        assert!(!external.path().join("experiment-owner.lock").exists());
        #[cfg(windows)]
        std::fs::remove_dir(&link).unwrap();
        #[cfg(unix)]
        std::fs::remove_file(&link).unwrap();
    }
}
