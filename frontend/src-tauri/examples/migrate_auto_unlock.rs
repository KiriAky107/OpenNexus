use notesagent_host::credentials::CredentialBroker;
use std::path::PathBuf;

fn main() -> Result<(), String> {
    let mut arguments = std::env::args_os().skip(1);
    let vault = PathBuf::from(arguments.next().ok_or("TARGET_REQUIRED")?);
    let legacy = PathBuf::from(arguments.next().ok_or("LEGACY_REQUIRED")?);
    if arguments.next().is_some() {
        return Err("ARGUMENTS_INVALID".into());
    }
    let parent = vault.parent().ok_or("TARGET_INVALID")?;
    let backup = parent.join("stronghold.pre-0.5.0.onxcred");
    let auto_key = parent.join("auto-unlock.dpapi");
    if auto_key.exists() {
        return Err("AUTO_UNLOCK_ALREADY_CONFIGURED".into());
    }
    if backup.exists() {
        return Err("BACKUP_ALREADY_EXISTS".into());
    }
    if vault.exists() {
        std::fs::rename(&vault, &backup).map_err(|_| "BACKUP_FAILED")?;
    }
    let migrated = (|| {
        let mut broker = CredentialBroker::new(vault.clone());
        if !broker.ensure_system_unlock()? {
            return Err("AUTO_UNLOCK_INITIALIZATION_FAILED".into());
        }
        broker.import_fernet(&legacy, None)
    })();
    match migrated {
        Ok(count) => {
            println!("Migrated {count} credential(s) to Windows automatic unlock.");
            Ok(())
        }
        Err(error) => {
            let _ = std::fs::remove_file(&vault);
            let _ = std::fs::remove_file(&auto_key);
            if backup.exists() {
                let _ = std::fs::rename(&backup, &vault);
            }
            Err(error)
        }
    }
}
