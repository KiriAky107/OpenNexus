use notesagent_host::credentials::{CredentialBroker, CredentialId, Scope};
use zeroize::Zeroizing;
fn password() -> Zeroizing<Vec<u8>> {
    Zeroizing::new(b"isolated-ownership-fixture".to_vec())
}
#[test]
fn stale_writer_is_rejected_then_handoff_preserves_both_commits() {
    let temp = tempfile::tempdir().unwrap();
    let path = temp.path().join("vault");
    let a = CredentialId::legacy("first");
    let b = CredentialId::legacy("second");
    let mut one = CredentialBroker::new(path.clone());
    one.unlock(password()).unwrap();
    let mut two = CredentialBroker::new(path.clone());
    assert_eq!(two.unlock(password()).unwrap_err(), "CREDENTIALS_BUSY");
    assert!(two.is_locked());
    one.put(&a, Zeroizing::new(b"fixture-a".to_vec())).unwrap();
    assert_eq!(
        two.put(&b, Zeroizing::new(b"fixture-b".to_vec()))
            .unwrap_err(),
        "CREDENTIALS_LOCKED"
    );
    one.lock();
    two.unlock(password()).unwrap();
    assert!(two.resolve(&Scope::Provider, &a).unwrap().is_some());
    two.put(&b, Zeroizing::new(b"fixture-b".to_vec())).unwrap();
    two.lock();
    // A failed password attempt must release its ownership too.
    assert!(one
        .unlock(Zeroizing::new(b"wrong-fixture-password".to_vec()))
        .is_err());
    two.unlock(password()).unwrap();
    assert!(two.resolve(&Scope::Provider, &a).unwrap().is_some());
    assert!(two.resolve(&Scope::Provider, &b).unwrap().is_some());
}

#[test]
fn process_lock_is_exclusive_and_released_on_crash() {
    use std::io::{BufRead, Read, Write};
    use std::process::{Command, Stdio};
    if let Ok(role) = std::env::var("OPENNEXUS_OWNERSHIP_TEST_ROLE") {
        let path =
            std::path::PathBuf::from(std::env::var_os("OPENNEXUS_OWNERSHIP_TEST_PATH").unwrap());
        let mut broker = CredentialBroker::new(path);
        if role == "blocked" {
            assert_eq!(broker.unlock(password()).unwrap_err(), "CREDENTIALS_BUSY");
            return;
        }
        broker.unlock(password()).unwrap();
        broker
            .put(
                &CredentialId::legacy("child"),
                Zeroizing::new(b"child-fixture".to_vec()),
            )
            .unwrap();
        println!("OWNERSHIP_READY");
        std::io::stdout().flush().unwrap();
        let _ = std::io::stdin().read_exact(&mut [0]);
        return;
    }
    let temp = tempfile::tempdir().unwrap();
    let path = temp.path().join("vault");
    let mut broker = CredentialBroker::new(path.clone());
    broker.unlock(password()).unwrap();
    let command = |role: &str| {
        let mut c = Command::new(std::env::current_exe().unwrap());
        c.args([
            "--exact",
            "process_lock_is_exclusive_and_released_on_crash",
            "--nocapture",
        ])
        .env("OPENNEXUS_OWNERSHIP_TEST_ROLE", role)
        .env("OPENNEXUS_OWNERSHIP_TEST_PATH", &path);
        c
    };
    assert!(command("blocked")
        .stdout(Stdio::null())
        .status()
        .unwrap()
        .success());
    broker.lock();
    let mut child = command("owner")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .spawn()
        .unwrap();
    let output = child.stdout.take().unwrap();
    let (sender, receiver) = std::sync::mpsc::channel();
    std::thread::spawn(move || {
        for line in std::io::BufReader::new(output)
            .lines()
            .map_while(Result::ok)
        {
            if line == "OWNERSHIP_READY" {
                let _ = sender.send(());
                break;
            }
        }
    });
    let ready = receiver
        .recv_timeout(std::time::Duration::from_secs(30))
        .is_ok();
    if ready {
        assert_eq!(broker.unlock(password()).unwrap_err(), "CREDENTIALS_BUSY");
    }
    child.kill().unwrap();
    child.wait().unwrap();
    assert!(ready, "child never acquired ownership");
    broker.unlock(password()).unwrap();
    assert!(broker
        .resolve(&Scope::Provider, &CredentialId::legacy("child"))
        .unwrap()
        .is_some());
}
