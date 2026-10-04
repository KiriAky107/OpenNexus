//! Opt-in feasibility test only. No renderer/API execution entry is exposed.
use crate::{
    extension_container::Profile, extension_launch_data::LaunchData, extension_process::Suspended,
};
use sha2::{Digest, Sha256};
use std::{
    collections::BTreeMap,
    fs::{self, File, OpenOptions},
    os::windows::fs::{MetadataExt, OpenOptionsExt},
    path::{Path, PathBuf},
    time::{Duration, Instant},
};
use windows_sys::Win32::Storage::FileSystem::{
    FILE_FLAG_BACKUP_SEMANTICS, FILE_FLAG_OPEN_REPARSE_POINT, FILE_GENERIC_READ, WRITE_DAC,
};

struct Grants<'a> {
    profile: &'a Profile,
    files: Vec<File>,
}
impl<'a> Grants<'a> {
    fn new(profile: &'a Profile) -> Self {
        Self {
            profile,
            files: Vec::new(),
        }
    }
    fn pin(&mut self, path: &Path, expected: Option<&str>) {
        let mut file = OpenOptions::new()
            .access_mode(FILE_GENERIC_READ | WRITE_DAC)
            .share_mode(1)
            .custom_flags(FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT)
            .open(path)
            .unwrap();
        let metadata = file.metadata().unwrap();
        assert_eq!(metadata.file_attributes() & 0x400, 0, "reparse point");
        if let Some(expected) = expected {
            use std::io::{Read, Seek};
            let mut bytes = Vec::new();
            file.read_to_end(&mut bytes).unwrap();
            assert_eq!(format!("{:x}", Sha256::digest(&bytes)), expected);
            file.rewind().unwrap();
        }
        self.profile.grant_package_read_execute(&file).unwrap();
        self.files.push(file);
    }
    fn revoke(mut self) {
        for file in self.files.iter().rev() {
            self.profile.revoke_package_access(file).unwrap();
        }
        self.files.clear();
    }
}
impl Drop for Grants<'_> {
    fn drop(&mut self) {
        for file in self.files.iter().rev() {
            if let Err(error) = self.profile.revoke_package_access(file) {
                eprintln!("Probe ACL cleanup failed: {}", error.code);
            }
        }
    }
}

struct Scratch(PathBuf);
impl Drop for Scratch {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

#[test]
#[ignore = "requires prepared official embedded runtime; run scripts/verify-experiment-runtime.ps1"]
fn packaged_python_isolation_and_owned_process_tree() {
    native_probe(false);
}

#[test]
#[ignore = "requires prepared official embedded runtime; run scripts/verify-experiment-runtime.ps1"]
fn experiment_policy_bounds_the_complete_container() {
    native_probe(true);
}

fn native_probe(experimental: bool) {
    let limits = crate::experiment_policy::ExecutionLimits {
        wall_seconds: 8,
        cpu_seconds: 2,
        processes: 8,
        disk_mib: 8,
        output_mib: 1,
        ..Default::default()
    };
    let validated = limits.validate().unwrap();
    let root = Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .unwrap()
        .parent()
        .unwrap();
    let lock: serde_json::Value = serde_json::from_str(include_str!(
        "../../../scripts/experiment-runtime-lock.json"
    ))
    .unwrap();
    let runtime = root
        .join(".build/experiment-runtime")
        .join(lock["runtime_id"].as_str().unwrap());
    let manifest: serde_json::Value =
        serde_json::from_slice(&fs::read(runtime.join("runtime.json")).unwrap()).unwrap();
    assert_eq!(manifest["lock"], lock);
    assert!(
        std::env::var_os("OPENNEXUS_PROBE_PARENT_TOKEN").is_some(),
        "wrapper must set synthetic parent token"
    );
    let inputs = tempfile::Builder::new()
        .prefix("probe-input-")
        .tempdir_in(root.join(".build/experiment-runtime"))
        .unwrap();
    let private = tempfile::Builder::new()
        .prefix("probe-private-")
        .tempdir_in(root.join(".build/experiment-runtime"))
        .unwrap();
    let sentinel = private.path().join("private.txt");
    fs::write(&sentinel, b"synthetic private data").unwrap();
    let script = inputs.path().join("main.py");
    fs::write(
        &script,
        include_str!("../../../scripts/fixtures/experiment_runtime_probe.py"),
    )
    .unwrap();
    let csv = "name,value\r\n中文,7\r\nexample,11\r\n";
    fs::write(inputs.path().join("input.csv"), csv).unwrap();
    let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
    listener.set_nonblocking(true).unwrap();
    // Positive control: the very same listening endpoint is reachable by Host.
    let control = std::net::TcpStream::connect(listener.local_addr().unwrap()).unwrap();
    let (accepted, _) = listener.accept().unwrap();
    drop(accepted);
    drop(control);
    let profile = Profile::create().unwrap();
    let folder = profile.folder().unwrap();
    // Windows supplies this physical TEMP to the AppContainer process. A custom
    // name in the launch block is not a reliable output location for CPython.
    let scratch = Scratch(folder.join("Temp"));
    fs::create_dir_all(&scratch.0).unwrap();
    let mut grants = Grants::new(&profile);
    grants.pin(&runtime, None);
    for (name, entry) in manifest["files"].as_object().unwrap() {
        assert!(!name.contains(['/', '\\']), "flat runtime inventory");
        grants.pin(&runtime.join(name), Some(entry["sha256"].as_str().unwrap()));
    }
    grants.pin(inputs.path(), None);
    grants.pin(&script, None);
    grants.pin(&inputs.path().join("input.csv"), None);
    let executable = runtime.join("python.exe");
    let system = PathBuf::from(std::env::var_os("SystemRoot").unwrap());
    let launch = |mode: &str| {
        let arguments = vec![
            "-I".into(),
            "-B".into(),
            "-X".into(),
            "utf8".into(),
            script.to_string_lossy().into_owned(),
            mode.into(),
            inputs.path().to_string_lossy().into_owned(),
            sentinel.to_string_lossy().into_owned(),
            listener.local_addr().unwrap().port().to_string(),
        ];
        let data = LaunchData::new(
            &executable,
            &arguments,
            &system,
            &folder,
            &scratch.0,
            &BTreeMap::new(),
        )
        .unwrap();
        let suspended = if experimental {
            Suspended::create_experiment(&profile, &executable, data, &validated).unwrap()
        } else {
            Suspended::create(&profile, &executable, data).unwrap()
        };
        // Safety: the test owns synthetic inputs, pins the verified runtime and
        // entry for the entire process lifetime, and uses no network capabilities
        // or brokers. Production execution still needs independent authorization.
        unsafe { suspended.resume().unwrap() }
    };
    let running = launch("basic");
    let code = running.wait(Duration::from_secs(15)).unwrap();
    let error = fs::read_to_string(scratch.0.join("error.txt")).unwrap_or_default();
    assert_eq!(code, Some(0), "Python startup/fixture failed: {error}");
    let report: serde_json::Value =
        serde_json::from_slice(&fs::read(scratch.0.join("probe.json")).unwrap()).unwrap();
    assert_eq!(report["total"], 18);
    assert_eq!(report["names"][0], "中文");
    assert_eq!(report["runtime"], lock["version"]);
    assert_eq!(report["isolated"], 1);
    assert_eq!(report["site_loaded"], false);
    assert_eq!(report["input_write"]["denied"], true);
    assert_eq!(report["input_write"]["errno"], 13);
    assert_eq!(report["outside_read"]["errno"], 13);
    assert_eq!(report["network"]["denied"], true);
    assert!(
        report["network"]["winerror"] == 10013
            || report["network"]["error_class"] == "TimeoutError",
        "unexpected network result: {}",
        report["network"]
    );
    assert_eq!(report["parent_environment_absent"], true);
    assert_eq!(
        listener.accept().unwrap_err().kind(),
        std::io::ErrorKind::WouldBlock
    );
    assert_eq!(
        fs::read_to_string(inputs.path().join("input.csv")).unwrap(),
        csv
    );
    assert!(folder.join("probe-outside-scratch.txt").exists());
    drop(running);

    let running = launch("cancel");
    let start = Instant::now();
    while !scratch.0.join("child-ready").exists() && start.elapsed() < Duration::from_secs(10) {
        std::thread::sleep(Duration::from_millis(25));
    }
    assert!(
        scratch.0.join("child-ready").exists(),
        "child did not start"
    );
    let cancelled_processes = running.active_test_processes().unwrap();
    assert!(cancelled_processes >= 2);
    running.terminate().unwrap();
    assert!(running.wait(Duration::from_secs(5)).unwrap().is_some());
    let start = Instant::now();
    while running.active_test_processes().unwrap() != 0 && start.elapsed() < Duration::from_secs(5)
    {
        std::thread::sleep(Duration::from_millis(25));
    }
    assert_eq!(running.active_test_processes().unwrap(), 0);
    drop(running);
    let mut resources = serde_json::Map::new();
    let mut resource_probes = vec![
        ("memory", "EXTENSION_RESOURCE_MEMORY_EXCEEDED"),
        ("processes", "EXTENSION_RESOURCE_PROCESSES_EXCEEDED"),
        ("scratch", "EXTENSION_RESOURCE_SCRATCH_EXCEEDED"),
    ];
    if experimental {
        resource_probes.extend([
            ("cpu", "EXTENSION_RESOURCE_CPU_EXCEEDED"),
            ("outside", "EXTENSION_RESOURCE_SCRATCH_EXCEEDED"),
            ("file-stream", "EXTENSION_RESOURCE_SCRATCH_EXCEEDED"),
            ("directory-stream", "EXTENSION_RESOURCE_SCRATCH_EXCEEDED"),
        ]);
    }
    for (mode, expected) in resource_probes {
        let _ = fs::remove_file(scratch.0.join("error.txt"));
        let running = launch(mode);
        let deadline = running
            .start_test_tool_call(Duration::from_secs(15))
            .unwrap();
        let start = Instant::now();
        let outcome = loop {
            if let Err(error) = running.check_authorization() {
                break error.code;
            }
            if start.elapsed() > Duration::from_secs(12) {
                let _ = running.terminate();
                panic!(
                    "resource probe {mode} did not trip; fixture: {}",
                    fs::read_to_string(scratch.0.join("error.txt")).unwrap_or_default()
                );
            }
            std::thread::sleep(Duration::from_millis(25));
        };
        assert_eq!(outcome, expected);
        assert!(running.wait(Duration::from_secs(5)).unwrap().is_some());
        let start = Instant::now();
        while running.active_test_processes().unwrap() != 0
            && start.elapsed() < Duration::from_secs(5)
        {
            std::thread::sleep(Duration::from_millis(25));
        }
        assert_eq!(running.active_test_processes().unwrap(), 0);
        resources.insert(
            mode.into(),
            serde_json::json!({"error": outcome, "remaining_processes": 0}),
        );
        drop(deadline);
        drop(running);
        if mode == "scratch" {
            fs::remove_file(scratch.0.join("large.bin")).unwrap();
        }
        if mode == "outside" || mode == "file-stream" {
            fs::remove_file(folder.join("outside-large.bin")).unwrap();
        }
        if mode == "directory-stream" {
            fs::remove_dir(folder.join("stream-directory")).unwrap();
        }
    }
    let running = launch("child");
    let deadline = running
        .start_test_tool_call(Duration::from_millis(750))
        .unwrap();
    assert!(running.wait(Duration::from_secs(5)).unwrap().is_some());
    assert_eq!(
        deadline.check().unwrap_err().code,
        "EXTENSION_TOOL_DEADLINE_EXCEEDED"
    );
    assert_eq!(running.active_test_processes().unwrap(), 0);
    drop(deadline);
    drop(running);
    let independent_deadline = if experimental {
        fs::remove_file(scratch.0.join("child-ready")).unwrap();
        let running = launch("cancel");
        let start = Instant::now();
        while !scratch.0.join("child-ready").exists() && start.elapsed() < Duration::from_secs(5) {
            std::thread::sleep(Duration::from_millis(25));
        }
        assert!(scratch.0.join("child-ready").exists());
        let processes = running.active_test_processes().unwrap();
        assert!(
            processes >= 2,
            "independent deadline needs a real descendant"
        );
        // Do not start a test tool timer or poll authorization while waiting:
        // the run's own watchdog must kill the complete tree independently.
        assert!(running.wait(Duration::from_secs(12)).unwrap().is_some());
        assert_eq!(
            running.check_authorization().unwrap_err().code,
            "EXTENSION_TOOL_DEADLINE_EXCEEDED"
        );
        let start = Instant::now();
        while running.active_test_processes().unwrap() != 0
            && start.elapsed() < Duration::from_secs(5)
        {
            std::thread::sleep(Duration::from_millis(25));
        }
        assert_eq!(running.active_test_processes().unwrap(), 0);
        drop(running);
        Some(serde_json::json!({"seconds": limits.wall_seconds,
            "initial_processes": processes, "remaining_processes": 0}))
    } else {
        None
    };
    let receipt = if experimental {
        "policy-probe-result.json"
    } else {
        "probe-result.json"
    };
    fs::write(root.join(".build/experiment-runtime").join(receipt), serde_json::to_vec_pretty(&serde_json::json!({
        "schema_version": 1, "runtime_id": lock["runtime_id"], "basic": report, "cancelled_tree_processes": cancelled_processes,
        "remaining_processes": 0, "resources": resources, "deadline_ms": 750,
        "experiment_limits": if experimental { Some(&limits) } else { None },
        "independent_run_deadline_seconds": independent_deadline,
        "host_network_positive_control": true, "container_outside_scratch_writable": true,
        "production_executor_enabled": false
    })).unwrap()).unwrap();
    grants.revoke();
    drop(scratch);
    profile.remove().unwrap();
}
