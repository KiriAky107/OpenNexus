//! Windows DPAPI-backed storage for the random Stronghold unlock secret.
use std::fs;
use std::io::Write;
use std::path::Path;
use windows_sys::Win32::Foundation::LocalFree;
use windows_sys::Win32::Security::Cryptography::{
    CryptProtectData, CryptUnprotectData, CRYPTPROTECT_UI_FORBIDDEN, CRYPT_INTEGER_BLOB,
};
use zeroize::Zeroizing;

type Result<T> = std::result::Result<T, String>;
const MAGIC: &[u8] = b"ONXDPAPI1";
const ENTROPY: &[u8] = b"OpenNexus credential auto-unlock v1";

fn transform(data: &[u8], protect: bool) -> Result<Zeroizing<Vec<u8>>> {
    let input = CRYPT_INTEGER_BLOB {
        cbData: u32::try_from(data.len()).map_err(|_| "CREDENTIAL_AUTO_UNLOCK_FAILED")?,
        pbData: data.as_ptr() as *mut u8,
    };
    let entropy = CRYPT_INTEGER_BLOB {
        cbData: ENTROPY.len() as u32,
        pbData: ENTROPY.as_ptr() as *mut u8,
    };
    let mut output = CRYPT_INTEGER_BLOB::default();
    let ok = unsafe {
        if protect {
            CryptProtectData(
                &input,
                std::ptr::null(),
                &entropy,
                std::ptr::null(),
                std::ptr::null(),
                CRYPTPROTECT_UI_FORBIDDEN,
                &mut output,
            )
        } else {
            CryptUnprotectData(
                &input,
                std::ptr::null_mut(),
                &entropy,
                std::ptr::null(),
                std::ptr::null(),
                CRYPTPROTECT_UI_FORBIDDEN,
                &mut output,
            )
        }
    };
    if ok == 0 || output.pbData.is_null() || output.cbData == 0 {
        return Err("CREDENTIAL_AUTO_UNLOCK_FAILED".into());
    }
    let result = unsafe {
        Zeroizing::new(std::slice::from_raw_parts(output.pbData, output.cbData as usize).to_vec())
    };
    if !protect {
        unsafe { std::ptr::write_bytes(output.pbData, 0, output.cbData as usize) };
    }
    unsafe { LocalFree(output.pbData as *mut core::ffi::c_void) };
    Ok(result)
}

pub fn load(path: &Path) -> Result<Option<Zeroizing<Vec<u8>>>> {
    if !path.exists() {
        return Ok(None);
    }
    let metadata = fs::symlink_metadata(path).map_err(|_| "CREDENTIAL_AUTO_UNLOCK_FAILED")?;
    if !metadata.is_file() || metadata.file_type().is_symlink() || metadata.len() > 64 * 1024 {
        return Err("CREDENTIAL_AUTO_UNLOCK_FAILED".into());
    }
    let bytes = fs::read(path).map_err(|_| "CREDENTIAL_AUTO_UNLOCK_FAILED")?;
    if !bytes.starts_with(MAGIC) || bytes.len() == MAGIC.len() {
        return Err("CREDENTIAL_AUTO_UNLOCK_FAILED".into());
    }
    transform(&bytes[MAGIC.len()..], false).map(Some)
}

pub fn save(path: &Path, secret: &[u8]) -> Result<()> {
    let protected = transform(secret, true)?;
    let parent = path.parent().ok_or("CREDENTIAL_AUTO_UNLOCK_FAILED")?;
    fs::create_dir_all(parent).map_err(|_| "CREDENTIAL_AUTO_UNLOCK_FAILED")?;
    let mut target =
        tempfile::NamedTempFile::new_in(parent).map_err(|_| "CREDENTIAL_AUTO_UNLOCK_FAILED")?;
    target
        .write_all(MAGIC)
        .and_then(|_| target.write_all(&protected))
        .and_then(|_| target.as_file().sync_all())
        .map_err(|_| "CREDENTIAL_AUTO_UNLOCK_FAILED")?;
    target
        .persist(path)
        .map_err(|_| "CREDENTIAL_AUTO_UNLOCK_FAILED")?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn dpapi_round_trip_never_persists_plaintext() {
        let temp = tempfile::tempdir().unwrap();
        let path = temp.path().join("auto-unlock.dpapi");
        let secret = b"test-system-secret-123456789";
        save(&path, secret).unwrap();
        assert!(!fs::read(&path)
            .unwrap()
            .windows(secret.len())
            .any(|part| part == secret));
        assert_eq!(load(&path).unwrap().unwrap().as_slice(), secret);
    }
}
