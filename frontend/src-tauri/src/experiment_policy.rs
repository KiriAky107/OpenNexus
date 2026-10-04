//! Host-validated budgets, independent of extension permissions and defaults.
use crate::workspace::{HostError, Result};
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ExecutionLimits {
    pub wall_seconds: u32,
    pub cpu_seconds: u32,
    pub memory_mib: u32,
    pub processes: u32,
    pub disk_mib: u32,
    pub output_mib: u32,
    pub log_kib: u32,
    pub objects: u32,
}
impl Default for ExecutionLimits {
    fn default() -> Self {
        Self {
            wall_seconds: 60,
            cpu_seconds: 30,
            memory_mib: 256,
            processes: 4,
            disk_mib: 64,
            output_mib: 16,
            log_kib: 256,
            objects: 1024,
        }
    }
}
pub struct ValidatedLimits(ExecutionLimits);
impl ExecutionLimits {
    pub fn validate(&self) -> Result<ValidatedLimits> {
        if !(1..=120).contains(&self.wall_seconds)
            || !(1..=60).contains(&self.cpu_seconds)
            || self.cpu_seconds > self.wall_seconds
            || !(64..=512).contains(&self.memory_mib)
            || !(1..=8).contains(&self.processes)
            || !(8..=128).contains(&self.disk_mib)
            || self.output_mib == 0
            || self.output_mib > self.disk_mib
            || !(16..=1024).contains(&self.log_kib)
            || !(32..=2048).contains(&self.objects)
        {
            return Err(HostError::new("EXPERIMENT_LIMITS_INVALID"));
        }
        Ok(ValidatedLimits(self.clone()))
    }
}
impl ValidatedLimits {
    pub fn wall(&self) -> std::time::Duration {
        std::time::Duration::from_secs(self.0.wall_seconds.into())
    }
    pub fn cpu_ticks(&self) -> i64 {
        i64::from(self.0.cpu_seconds) * 10_000_000
    }
    pub fn memory_bytes(&self) -> usize {
        self.0.memory_mib as usize * 1024 * 1024
    }
    pub fn processes(&self) -> u32 {
        self.0.processes
    }
    pub fn disk_bytes(&self) -> u64 {
        u64::from(self.0.disk_mib) * 1024 * 1024
    }
    pub fn objects(&self) -> u32 {
        self.0.objects
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn rejects_unbounded_or_inconsistent_requests() {
        let base = ExecutionLimits::default();
        assert!(base.validate().is_ok());
        for invalid in [
            ExecutionLimits {
                wall_seconds: 0,
                ..base.clone()
            },
            ExecutionLimits {
                wall_seconds: 121,
                ..base.clone()
            },
            ExecutionLimits {
                cpu_seconds: 61,
                ..base.clone()
            },
            ExecutionLimits {
                memory_mib: 513,
                ..base.clone()
            },
            ExecutionLimits {
                processes: 0,
                ..base.clone()
            },
            ExecutionLimits {
                processes: 9,
                ..base.clone()
            },
            ExecutionLimits {
                disk_mib: 129,
                ..base.clone()
            },
            ExecutionLimits {
                output_mib: 65,
                ..base.clone()
            },
            ExecutionLimits {
                log_kib: 1025,
                ..base.clone()
            },
            ExecutionLimits {
                objects: 2049,
                ..base.clone()
            },
        ] {
            assert_eq!(
                invalid.validate().err().unwrap().code,
                "EXPERIMENT_LIMITS_INVALID"
            );
        }
        let mut request = serde_json::to_value(base).unwrap();
        request["network"] = serde_json::json!(true);
        assert!(serde_json::from_value::<ExecutionLimits>(request).is_err());
    }
}
