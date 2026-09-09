//! 确定性、有界依赖规划；没有突变、激活或自动下载。
use crate::{
    extension_package::Release,
    workspace::{hash, HostError, Result},
};
use semver::{Version, VersionReq};
use serde::Serialize;
use std::collections::{BTreeMap, BTreeSet};

pub struct Candidate {
    pub package_key: String,
    pub source: String,
    pub release: Release,
    pub signer_sha256: String,
}
#[derive(Clone, Debug, Serialize, PartialEq, Eq)]
pub struct LockedPackage {
    pub package_key: String,
    pub source: String,
    pub namespace: String,
    pub package_id: String,
    pub kind: String,
    pub version: String,
    pub archive_sha256: String,
    pub signer_sha256: String,
    pub release_sha256: String,
    pub permissions: Vec<String>,
}
#[derive(Debug, Serialize)]
pub struct Plan {
    pub fingerprint: String,
    pub packages: Vec<LockedPackage>,
}
fn error(code: &str) -> HostError {
    HostError::new(code)
}
fn name(release: &Release) -> String {
    format!("{}/{}", release.namespace, release.package_id)
}
fn dependency(owner: &Release, id: &str) -> Result<String> {
    let resolved = if id.contains('/') {
        id.to_owned()
    } else {
        format!("{}/{id}", owner.namespace)
    };
    let parts: Vec<_> = resolved.split('/').collect();
    if parts.len() != 2
        || parts.iter().any(|s| {
            !(2..=64).contains(&s.len())
                || !s.as_bytes()[0].is_ascii_alphanumeric()
                || !s
                    .bytes()
                    .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'-')
        })
    {
        return Err(error("EXTENSION_DEPENDENCY_INVALID"));
    }
    Ok(resolved)
}
fn requirement(value: &str) -> Result<VersionReq> {
    let value = if Version::parse(value).is_ok() {
        format!("={value}")
    } else {
        value.to_owned()
    };
    VersionReq::parse(&value).map_err(|_| error("EXTENSION_DEPENDENCY_REQUIREMENT"))
}
type Chosen = BTreeMap<String, usize>;
struct Solver<'a> {
    candidates: &'a [Candidate],
    groups: BTreeMap<String, Vec<usize>>,
    root: usize,
    remaining: usize,
    failure: &'static str,
}
impl Solver<'_> {
    fn solve(
        &mut self,
        chosen: Chosen,
        requirements: BTreeMap<String, Vec<VersionReq>>,
    ) -> Result<Option<Chosen>> {
        if self.remaining == 0 {
            return Err(error("EXTENSION_DEPENDENCY_COMPLEXITY"));
        }
        self.remaining -= 1;
        if requirements.len() > 200 {
            return Err(error("EXTENSION_DEPENDENCY_COMPLEXITY"));
        }
        for (key, index) in &chosen {
            let version = Version::parse(&self.candidates[*index].release.version)
                .map_err(|_| error("EXTENSION_DEPENDENCY_VERSION"))?;
            if requirements
                .get(key)
                .is_some_and(|all| all.iter().any(|r| !r.matches(&version)))
            {
                return Ok(None);
            }
        }
        let Some((next, constraints)) = requirements
            .iter()
            .find(|(key, _)| !chosen.contains_key(*key))
        else {
            if order(self.candidates, &chosen, self.root).is_err() {
                self.failure = "EXTENSION_DEPENDENCY_CYCLE";
                return Ok(None);
            }
            return Ok(Some(chosen));
        };
        let Some(indices) = self.groups.get(next).cloned() else {
            self.failure = "EXTENSION_DEPENDENCY_MISSING";
            return Ok(None);
        };
        for index in indices {
            if next == &name(&self.candidates[self.root].release) && index != self.root {
                continue;
            }
            let candidate = &self.candidates[index];
            let version = Version::parse(&candidate.release.version)
                .map_err(|_| error("EXTENSION_DEPENDENCY_VERSION"))?;
            if constraints.iter().any(|r| !r.matches(&version)) {
                continue;
            }
            let mut selected = chosen.clone();
            selected.insert(next.clone(), index);
            let mut required = requirements.clone();
            for (id, range) in &candidate.release.dependencies {
                required
                    .entry(dependency(&candidate.release, id)?)
                    .or_default()
                    .push(requirement(range)?);
            }
            if let Some(solution) = self.solve(selected, required)? {
                return Ok(Some(solution));
            }
        }
        Ok(None)
    }
}
fn order(candidates: &[Candidate], chosen: &Chosen, root: usize) -> Result<Vec<usize>> {
    fn visit(
        index: usize,
        candidates: &[Candidate],
        chosen: &Chosen,
        active: &mut BTreeSet<usize>,
        done: &mut BTreeSet<usize>,
        output: &mut Vec<usize>,
    ) -> Result<()> {
        if done.contains(&index) {
            return Ok(());
        }
        if !active.insert(index) {
            return Err(error("EXTENSION_DEPENDENCY_CYCLE"));
        }
        for id in candidates[index].release.dependencies.keys() {
            let key = dependency(&candidates[index].release, id)?;
            visit(
                *chosen
                    .get(&key)
                    .ok_or_else(|| error("EXTENSION_DEPENDENCY_MISSING"))?,
                candidates,
                chosen,
                active,
                done,
                output,
            )?;
        }
        active.remove(&index);
        done.insert(index);
        output.push(index);
        Ok(())
    }
    let mut output = Vec::new();
    visit(
        root,
        candidates,
        chosen,
        &mut BTreeSet::new(),
        &mut BTreeSet::new(),
        &mut output,
    )?;
    Ok(output)
}
pub fn plan(
    candidates: &[Candidate],
    root_key: &str,
    app_version: &str,
    platform: &str,
    architecture: &str,
) -> Result<Plan> {
    if candidates.len() > 4096 {
        return Err(error("EXTENSION_DEPENDENCY_COMPLEXITY"));
    }
    let app = Version::parse(app_version).map_err(|_| error("EXTENSION_APP_VERSION"))?;
    let root = candidates
        .iter()
        .position(|v| v.package_key == root_key)
        .ok_or_else(|| error("EXTENSION_DEPENDENCY_MISSING"))?;
    let mut groups: BTreeMap<String, Vec<usize>> = BTreeMap::new();
    for (index, candidate) in candidates.iter().enumerate() {
        if candidate.source != candidates[root].source {
            continue;
        }
        let r = &candidate.release;
        r.validate()?;
        let minimum =
            Version::parse(&r.min_app_version).map_err(|_| error("EXTENSION_APP_VERSION"))?;
        let maximum = r
            .max_app_version
            .as_ref()
            .map(|v| Version::parse(v).map_err(|_| error("EXTENSION_APP_VERSION")))
            .transpose()?;
        if app < minimum
            || maximum.is_some_and(|max| app > max)
            || (!r.platforms.is_empty() && !r.platforms.iter().any(|v| v == platform))
            || (!r.architectures.is_empty() && !r.architectures.iter().any(|v| v == architecture))
        {
            if index == root {
                return Err(error("EXTENSION_PLATFORM_INCOMPATIBLE"));
            }
            continue;
        }
        Version::parse(&r.version).map_err(|_| error("EXTENSION_DEPENDENCY_VERSION"))?;
        groups.entry(name(r)).or_default().push(index);
    }
    for indices in groups.values_mut() {
        indices.sort_by(|a, b| {
            Version::parse(&candidates[*b].release.version)
                .unwrap()
                .cmp(&Version::parse(&candidates[*a].release.version).unwrap())
                .then(candidates[*a].package_key.cmp(&candidates[*b].package_key))
        });
    }
    let mut solver = Solver {
        candidates,
        groups,
        root,
        remaining: 10000,
        failure: "EXTENSION_DEPENDENCY_CONFLICT",
    };
    let requirements = BTreeMap::from([(
        name(&candidates[root].release),
        vec![requirement(&candidates[root].release.version)?],
    )]);
    let chosen = solver
        .solve(BTreeMap::new(), requirements)?
        .ok_or_else(|| error(solver.failure))?;
    let packages: Vec<_> = order(candidates, &chosen, root)?
        .into_iter()
        .map(|i| {
            let c = &candidates[i];
            let r = &c.release;
            LockedPackage {
                package_key: c.package_key.clone(),
                source: c.source.clone(),
                namespace: r.namespace.clone(),
                package_id: r.package_id.clone(),
                kind: r.kind.clone(),
                version: r.version.clone(),
                archive_sha256: r.sha256.clone(),
                signer_sha256: c.signer_sha256.clone(),
                release_sha256: hash(&serde_json::to_vec(r).unwrap()),
                permissions: r.permissions.clone(),
            }
        })
        .collect();
    let encoded = serde_json::to_vec(&(&packages, app_version, platform, architecture)).unwrap();
    if encoded.len() > 4 * 1024 * 1024 {
        return Err(error("EXTENSION_DEPENDENCY_COMPLEXITY"));
    }
    let fingerprint = hash(&encoded);
    Ok(Plan {
        fingerprint,
        packages,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::Value;
    fn candidate(id: &str, version: &str, deps: &[(&str, &str)]) -> Candidate {
        let mut data: Value = serde_json::from_str(include_str!(
            "../../src/services/fixtures/community-python-vector.json"
        ))
        .unwrap();
        for key in ["release_id", "withdrawn", "download_path"] {
            data["release"].as_object_mut().unwrap().remove(key);
        }
        let mut release: Release = serde_json::from_value(data["release"].clone()).unwrap();
        release.package_id = id.into();
        release.version = version.into();
        release.dependencies = deps
            .iter()
            .map(|(k, v)| (k.to_string(), v.to_string()))
            .collect();
        Candidate {
            package_key: format!("{id}-{version}"),
            source: "https://catalog.example/".into(),
            release,
            signer_sha256: "trusted-test-key".into(),
        }
    }
    fn resolve(candidates: &[Candidate]) -> Result<Plan> {
        plan(candidates, "root-1.0.0", "0.3.0", "windows", "x86_64")
    }
    #[test]
    fn diamond_backtracks_and_orders_dependencies_before_users_deterministically() {
        let mut candidates = vec![
            candidate("root", "1.0.0", &[("aa", "*"), ("bb", "1.0.0")]),
            candidate("aa", "2.0.0", &[("cc", "^2.0.0")]),
            candidate("aa", "1.0.0", &[("cc", "^1.0.0")]),
            candidate("bb", "1.0.0", &[("cc", "^1.0.0")]),
            candidate("cc", "2.0.0", &[]),
            candidate("cc", "1.0.0", &[]),
        ];
        let first = resolve(&candidates).unwrap();
        assert_eq!(
            first
                .packages
                .iter()
                .map(|p| p.package_key.as_str())
                .collect::<Vec<_>>(),
            vec!["cc-1.0.0", "aa-1.0.0", "bb-1.0.0", "root-1.0.0"]
        );
        candidates.reverse();
        let second = resolve(&candidates).unwrap();
        assert_eq!(first.fingerprint, second.fingerprint);
        candidates
            .iter_mut()
            .find(|c| c.release.package_id == "root")
            .unwrap()
            .release
            .description = "changed signed metadata".into();
        assert_ne!(first.fingerprint, resolve(&candidates).unwrap().fingerprint);
    }
    #[test]
    fn cycles_missing_versions_and_incompatible_platforms_fail() {
        let cycle = [
            candidate("root", "1.0.0", &[("aa", "*")]),
            candidate("aa", "1.0.0", &[("root", "*")]),
        ];
        assert_eq!(
            resolve(&cycle).unwrap_err().code,
            "EXTENSION_DEPENDENCY_CYCLE"
        );
        assert_eq!(
            resolve(&[candidate("root", "1.0.0", &[("aa", "*")])])
                .unwrap_err()
                .code,
            "EXTENSION_DEPENDENCY_MISSING"
        );
        assert_eq!(
            resolve(&[
                candidate("root", "1.0.0", &[("aa", "1.0.0")]),
                candidate("aa", "1.1.0", &[])
            ])
            .unwrap_err()
            .code,
            "EXTENSION_DEPENDENCY_CONFLICT"
        );
        let mut root = candidate("root", "1.0.0", &[]);
        root.release.min_app_version = "9.0.0".into();
        assert_eq!(
            resolve(&[root]).unwrap_err().code,
            "EXTENSION_PLATFORM_INCOMPATIBLE"
        );
        let root = candidate("root", "1.0.0", &[]);
        assert!(plan(&[root], "root-1.0.0", "0.3.0", "linux", "x86_64").is_err());
    }
    #[test]
    fn sources_and_namespaces_are_not_dependency_substitutes() {
        let root = candidate("root", "1.0.0", &[("aa", "*")]);
        let mut other = candidate("aa", "1.0.0", &[]);
        other.source = "https://other.example/".into();
        assert_eq!(
            resolve(&[root, other]).unwrap_err().code,
            "EXTENSION_DEPENDENCY_MISSING"
        );
        let root = candidate("root", "1.0.0", &[("trusted/aa", "*")]);
        let mut other = candidate("aa", "1.0.0", &[]);
        other.release.namespace = "untrusted".into();
        assert_eq!(
            resolve(&[root, other]).unwrap_err().code,
            "EXTENSION_DEPENDENCY_MISSING"
        );
        let root = candidate("root", "1.0.0", &[("trusted/aa", "*")]);
        let mut trusted = candidate("aa", "1.0.0", &[]);
        trusted.release.namespace = "trusted".into();
        let accepted = resolve(&[root, trusted]).unwrap();
        assert_eq!(accepted.packages[0].namespace, "trusted");
        let root = candidate("root", "1.0.0", &[("../aa", "*")]);
        assert!(resolve(&[root]).is_err());
        let root = candidate("root", "1.0.0", &[("aa", "shell text")]);
        assert_eq!(
            resolve(&[root]).unwrap_err().code,
            "EXTENSION_DEPENDENCY_REQUIREMENT"
        );
    }
    #[test]
    fn deep_graph_is_bounded_without_leaving_a_partial_plan() {
        let mut candidates = vec![candidate("root", "1.0.0", &[("node-000", "*")])];
        for i in 0..210 {
            candidates.push(candidate(
                &format!("node-{i:03}"),
                "1.0.0",
                &[(&format!("node-{:03}", i + 1), "*")],
            ));
        }
        assert_eq!(
            resolve(&candidates).unwrap_err().code,
            "EXTENSION_DEPENDENCY_COMPLEXITY"
        );
        // 节点数合法但权限元数据过大的依赖图，也不能产生无界的 IPC 结果。
        let mut wide = vec![candidate("root", "1.0.0", &[])];
        for i in 0..100 {
            let id = format!("leaf-{i:03}");
            wide[0].release.dependencies.insert(id.clone(), "*".into());
            let mut leaf = candidate(&id, "1.0.0", &[]);
            leaf.release.permissions = (0..64)
                .map(|n| format!("{n:02}{}", "界".repeat(254)))
                .collect();
            wide.push(leaf);
        }
        // 拆分直接依赖项以遵守每个版本 64 个条目的限制。
        let second = wide[0].release.dependencies.split_off("leaf-050");
        wide[0]
            .release
            .dependencies
            .insert("branch".into(), "*".into());
        let mut branch = candidate("branch", "1.0.0", &[]);
        branch.release.dependencies = second;
        wide.push(branch);
        assert_eq!(
            resolve(&wide).unwrap_err().code,
            "EXTENSION_DEPENDENCY_COMPLEXITY"
        );
    }
}
