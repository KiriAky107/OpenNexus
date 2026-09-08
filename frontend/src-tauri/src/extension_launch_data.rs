//! Native argv/environment encoding. This does not authorize or launch a process.
use crate::workspace::{HostError, Result};
use std::{collections::BTreeMap, os::windows::ffi::OsStrExt, path::Path};
use zeroize::Zeroize;

pub struct LaunchData {
    command: Vec<u16>,
    environment: Vec<u16>,
}
impl Drop for LaunchData {
    fn drop(&mut self) {
        self.command.zeroize();
        self.environment.zeroize();
    }
}
impl LaunchData {
    /// The Host supplies verified absolute paths and explicitly declared/resolved
    /// environment values. Never reads the parent environment. CRT argv rules
    /// apply to native executables, not cmd.exe, batch files or shell interpreters.
    pub fn new(
        executable: &Path,
        arguments: &[String],
        system_root: &Path,
        local_app_data: &Path,
        scratch: &Path,
        declared: &BTreeMap<String, String>,
    ) -> Result<Self> {
        let bad = || HostError::new("EXTENSION_LAUNCH_DATA_INVALID");
        if !executable.is_absolute()
            || !executable
                .extension()
                .is_some_and(|v| v.eq_ignore_ascii_case("exe"))
            || arguments.len() > 128
            || declared.len() > 128
        {
            return Err(bad());
        }
        let executable: Vec<u16> = executable.as_os_str().encode_wide().collect();
        if executable.contains(&0) || executable.contains(&34) || executable.len() > 32764 {
            return Err(bad());
        }
        let mut result = Self {
            command: Vec::with_capacity(32767),
            environment: Vec::with_capacity(32767),
        };
        result.command.push(34);
        result.command.extend(executable);
        result.command.push(34);
        for argument in arguments {
            if argument.len() > 8192 || argument.contains('\0') {
                return Err(bad());
            }
            // Reserve the full bounded buffers once: do not leave earlier
            // copies of resolved values behind through Vec reallocations.
            let mut encoded_len = 0;
            let mut trailing = 0;
            for unit in argument.encode_utf16() {
                if unit == 92 {
                    trailing += 1;
                } else {
                    encoded_len += if unit == 34 {
                        trailing * 2 + 2
                    } else {
                        trailing + 1
                    };
                    trailing = 0;
                }
            }
            encoded_len += trailing * 2;
            if result.command.len() + 3 + encoded_len + 1 > 32767 {
                return Err(bad());
            }
            result.command.extend([32, 34]);
            let mut slashes = 0;
            for unit in argument.encode_utf16() {
                if unit == 92 {
                    slashes += 1;
                    continue;
                }
                result.command.extend(std::iter::repeat_n(
                    92,
                    if unit == 34 { slashes * 2 + 1 } else { slashes },
                ));
                result.command.push(unit);
                slashes = 0;
            }
            result.command.extend(std::iter::repeat_n(92, slashes * 2));
            result.command.push(34);
            if result.command.len() >= 32767 {
                return Err(bad());
            }
        }
        result.command.push(0);
        if result.command.len() > 32767 {
            return Err(bad());
        }
        // ASCII names give a deterministic Windows case-insensitive order.
        // Values remain borrowed until encoded so there are no secret clones.
        let mut fields: BTreeMap<String, &std::ffi::OsStr> = BTreeMap::new();
        for (name, path) in [
            ("SYSTEMROOT", system_root),
            ("LOCALAPPDATA", local_app_data),
            ("TEMP", scratch),
            ("TMP", scratch),
        ] {
            if !path.is_absolute() {
                return Err(bad());
            }
            fields.insert(name.to_owned(), path.as_os_str());
        }
        for (name, value) in declared {
            if name.is_empty()
                || name.len() > 128
                || !name.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'_')
                || value.len() > 8192
            {
                return Err(bad());
            }
            if fields
                .insert(name.to_ascii_uppercase(), value.as_ref())
                .is_some()
            {
                return Err(bad());
            }
        }
        for (name, value) in fields {
            if result.environment.len() + name.len() + value.encode_wide().count() + 3 > 32767 {
                return Err(bad());
            }
            result.environment.extend(name.encode_utf16());
            result.environment.push(61);
            for unit in value.encode_wide() {
                if unit == 0 {
                    return Err(bad());
                }
                result.environment.push(unit);
            }
            result.environment.push(0);
            if result.environment.len() >= 32767 {
                return Err(bad());
            }
        }
        result.environment.push(0);
        Ok(result)
    }
    pub(crate) fn command_mut(&mut self) -> &mut [u16] {
        &mut self.command
    }
    /// Pass with CREATE_UNICODE_ENVIRONMENT; never substitute a null pointer.
    pub fn environment(&self) -> &[u16] {
        &self.environment
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn build(args: &[String], vars: &BTreeMap<String, String>) -> Result<LaunchData> {
        LaunchData::new(
            Path::new(r"C:\package\probe.exe"),
            args,
            Path::new(r"C:\Windows"),
            Path::new(r"C:\container"),
            Path::new(r"C:\scratch"),
            vars,
        )
    }
    #[test]
    fn rejects_ambiguous_environment_names_reserved_overrides_nul_and_limits() {
        for fields in [
            vec![("temp", "x")],
            vec![("a", "1"), ("A", "2")],
            vec![("=C:", "x")],
            vec![("SAFE", "x\0y")],
        ] {
            assert!(build(
                &[],
                &fields
                    .into_iter()
                    .map(|(k, v)| (k.into(), v.into()))
                    .collect()
            )
            .is_err());
        }
        assert!(build(&["x\0y".into()], &BTreeMap::new()).is_err());
        assert!(build(&vec!["x".repeat(8192); 4], &BTreeMap::new()).is_err());
        assert!(build(
            &[],
            &(0..4)
                .map(|i| (format!("V{i}"), "x".repeat(8192)))
                .collect()
        )
        .is_err());
        assert!(build(&vec![String::new(); 129], &BTreeMap::new()).is_err());
    }
    #[test]
    fn refuses_relative_or_non_native_entry_and_preserves_wide_paths() {
        for entry in [
            r"relative.exe",
            r"C:\package\entry.cmd",
            "C:\\bad\"name.exe",
        ] {
            assert!(LaunchData::new(
                Path::new(entry),
                &[],
                Path::new(r"C:\Windows"),
                Path::new(r"C:\container"),
                Path::new(r"C:\scratch"),
                &BTreeMap::new()
            )
            .is_err());
        }
        use std::os::windows::ffi::OsStringExt;
        let wide = std::ffi::OsString::from_wide(&[67, 58, 92, 0xd800]);
        let data = LaunchData::new(
            Path::new(r"C:\probe.exe"),
            &[],
            Path::new(r"C:\Windows"),
            Path::new(&wide),
            Path::new(&wide),
            &BTreeMap::new(),
        )
        .unwrap();
        assert!(data.environment().contains(&0xd800));
        assert!(data.environment().ends_with(&[0, 0]));
    }
}
