//! Reconcile actual directory entries before changing any persistent identity.
use super::{supported, CachedEntry, Entry, HostError, Result, Stamp, Workspace};
use rusqlite::params;
use std::collections::{BTreeMap, BTreeSet};
use uuid::Uuid;

struct Previous {
    entry: Entry,
    identity: Option<String>,
}

#[cfg(windows)]
fn windows_identity_parts(identity: &str) -> Option<(&str, &str)> {
    let boundary = identity.match_indices(':').nth(2)?.0;
    Some((&identity[..boundary], &identity[boundary + 1..]))
}

#[cfg(windows)]
struct WindowsIdentityIndex<'a> {
    previous: &'a BTreeMap<String, Previous>,
    native: BTreeMap<&'a str, Option<&'a str>>,
    destinations: Option<BTreeMap<std::path::PathBuf, Vec<&'a Previous>>>,
}

#[cfg(windows)]
impl<'a> WindowsIdentityIndex<'a> {
    fn new(previous: &'a BTreeMap<String, Previous>) -> Self {
        let mut native = BTreeMap::new();
        for old in previous.values().filter(|old| !old.entry.deleted) {
            if let Some(identity) = old.identity.as_deref() {
                if let Some((index, _)) = windows_identity_parts(identity) {
                    // Multiple hard links cannot establish a unique source.
                    native
                        .entry(index)
                        .and_modify(|value| *value = None)
                        .or_insert(Some(identity));
                }
            }
        }
        Self {
            previous,
            native,
            destinations: None,
        }
    }

    fn contains_native(&self, identity: &str) -> bool {
        windows_identity_parts(identity).is_some_and(|(native, _)| self.native.contains_key(native))
    }

    fn tunneled_identity(
        &mut self,
        identity: &str,
        path: &str,
        mut canonical: impl FnMut(&str) -> Result<Option<std::path::PathBuf>>,
    ) -> Result<Option<&'a str>> {
        // Newly created files and ambiguous hard links never need filesystem
        // alias lookups. Only a previously known native index can be tunneled.
        let Some((native, birth)) = windows_identity_parts(identity) else {
            return Ok(None);
        };
        let Some(original) = self.native.get(native).copied().flatten() else {
            return Ok(None);
        };
        let matches = |old: &Previous| {
            old.identity
                .as_deref()
                .and_then(windows_identity_parts)
                .is_some_and(|(target_native, target_birth)| {
                    target_native != native && target_birth == birth
                })
        };
        if let Some(destination) = self.previous.get(path) {
            return Ok(matches(destination).then_some(original));
        }
        // Build actual filesystem aliases once per reconciliation, only if a
        // known source needs a changed-case destination lookup. Case-sensitive
        // Windows directories remain distinct; no Unicode folding is involved.
        if self.destinations.is_none() {
            let mut destinations: BTreeMap<_, Vec<_>> = BTreeMap::new();
            for old in self
                .previous
                .values()
                .filter(|old| old.identity.is_some() && !old.entry.path.starts_with(".ainote/"))
            {
                if let Some(path) = canonical(&old.entry.path)? {
                    destinations.entry(path).or_default().push(old);
                }
            }
            self.destinations = Some(destinations);
        }
        let Some(path) = canonical(path)? else {
            return Ok(None);
        };
        Ok(self
            .destinations
            .as_ref()
            .and_then(|destinations| destinations.get(&path))
            .is_some_and(|entries| entries.iter().any(|old| matches(old)))
            .then_some(original))
    }
}

impl Workspace {
    pub(crate) fn refresh_read_identity(&mut self, path: &str) -> Result<()> {
        if supported(path) && self.identity_rescan_needed(&BTreeSet::from([path.to_owned()]))? {
            self.changed_path(path);
            self.refresh_snapshot(false)?;
        }
        Ok(())
    }

    pub(super) fn identity_rescan_needed(&self, dirty: &BTreeSet<String>) -> Result<bool> {
        for path in dirty {
            if !supported(path) && !self.root.join(path).is_dir() {
                // A removed directory can contain tracked files.
                if self.cache.entries.contains_key(path) {
                    return Ok(true);
                }
                continue;
            }
            let target = self.resolve(path)?;
            if !target.exists() || target.is_dir() || !self.cache.entries.contains_key(path) {
                return Ok(true);
            }
            #[cfg(windows)]
            if target.canonicalize()? != target {
                return Ok(true);
            }
            let identity = Stamp::read(&target)?.identity;
            if self.cache.entries[path]
                .stamp
                .as_ref()
                .is_none_or(|old| old.identity != identity)
            {
                return Ok(true);
            }
        }
        Ok(false)
    }

    pub(super) fn reconcile_file_identities(
        &mut self,
        next: &mut BTreeMap<String, CachedEntry>,
        enumerated: bool,
    ) -> Result<()> {
        let previous = {
            let mut statement = self.db.prepare(
                "SELECT f.id,f.path,f.hash,f.revision,f.deleted,i.identity FROM files f
                 LEFT JOIN file_disk_identity i ON i.file_id=f.id",
            )?;
            let rows = statement.query_map([], |row| {
                let path: String = row.get(1)?;
                Ok((
                    path.clone(),
                    Previous {
                        entry: Entry {
                            file_id: row.get(0)?,
                            path,
                            hash: row.get(2)?,
                            revision: row.get(3)?,
                            deleted: row.get(4)?,
                            is_folder: false,
                            updated_at: None,
                        },
                        identity: row.get(5)?,
                    },
                ))
            })?;
            rows.collect::<std::result::Result<BTreeMap<_, _>, _>>()?
        };
        let previous_by_id: BTreeMap<_, _> = previous
            .values()
            .map(|old| (old.entry.file_id.as_str(), old))
            .collect();
        let mut old_identities: BTreeMap<&str, Vec<&Previous>> = BTreeMap::new();
        for old in previous.values().filter(|old| !old.entry.deleted) {
            if let Some(identity) = old.identity.as_deref() {
                old_identities.entry(identity).or_default().push(old);
            }
        }
        #[cfg(windows)]
        let mut windows_identities = WindowsIdentityIndex::new(&previous);
        let mut current_identities: BTreeMap<String, Vec<String>> = BTreeMap::new();
        let mut matched_identities = BTreeMap::new();
        for (path, item) in next.iter() {
            if let Some(identity) = item.stamp.as_ref().and_then(|s| s.identity.as_ref()) {
                #[cfg(windows)]
                let identity = if old_identities.contains_key(identity.as_str()) {
                    identity.as_str()
                } else {
                    windows_identities
                        .tunneled_identity(identity, path, |path| {
                            let target = self.resolve(path)?;
                            if target.exists() {
                                Ok(Some(target.canonicalize()?))
                            } else {
                                Ok(None)
                            }
                        })?
                        .unwrap_or(identity.as_str())
                };
                matched_identities.insert(path.clone(), identity.to_owned());
                current_identities
                    .entry(identity.to_owned())
                    .or_default()
                    .push(path.clone());
            }
        }
        let mut assigned = BTreeSet::new();
        for (path, item) in next.iter_mut().filter(|(_, item)| !item.entry.is_folder) {
            let same_path = previous.get(path).filter(|old| !old.entry.deleted);
            let identity = matched_identities.get(path).map(String::as_str);
            let locations = identity.and_then(|id| current_identities.get(id));
            let candidates: Vec<_> = identity
                .and_then(|id| old_identities.get(id))
                .into_iter()
                .flatten()
                .copied()
                .filter(|old| {
                    &old.entry.path != path
                        && locations.is_some_and(|paths| !paths.contains(&old.entry.path))
                })
                .collect();
            let unchanged_identity = same_path
                .is_some_and(|old| identity.is_some() && old.identity.as_deref() == identity);
            let new_locations = locations.map_or(0, |paths| {
                paths
                    .iter()
                    .filter(|path| {
                        previous.get(*path).is_none_or(|old| {
                            old.entry.deleted || old.identity.as_deref() != identity
                        })
                    })
                    .count()
            });
            let old = if unchanged_identity {
                same_path
            } else if candidates.len() > 1 || (!candidates.is_empty() && new_locations > 1) {
                // Multiple hard links / reused identities cannot establish which
                // logical document moved. Keep the previous snapshot intact.
                return Err(HostError::new("FILE_IDENTITY_CONFLICT"));
            } else if let Some(source) = candidates.first() {
                Some(*source)
            } else {
                #[cfg(windows)]
                if same_path.is_some()
                    && identity.is_some_and(|identity| windows_identities.contains_native(identity))
                {
                    return Err(HostError::new("FILE_IDENTITY_CONFLICT"));
                }
                // Replacing a file atomically at its existing path is an edit.
                // If the original object moved elsewhere, this is a new file.
                same_path.filter(|old| {
                    old.identity
                        .as_ref()
                        .and_then(|id| current_identities.get(id))
                        .is_none_or(|paths| paths.contains(path))
                })
            };
            item.entry.file_id = old.map_or_else(
                || Uuid::new_v4().to_string(),
                |old| old.entry.file_id.clone(),
            );
            item.entry.revision = old.map_or(1, |old| {
                old.entry.revision
                    + i64::from(old.entry.path != *path || old.entry.hash != item.entry.hash)
            });
            if !assigned.insert(item.entry.file_id.clone()) {
                return Err(HostError::new("FILE_IDENTITY_CONFLICT"));
            }
        }

        // Reserve all moved paths first, then retire overwritten tombstones and
        // install the new mapping. A DB error rolls back the entire snapshot.
        // Historical outbox/observed paths deliberately remain untouched: sync
        // still needs to send the old deletion before a new occupant's put.
        let tx = self.db.transaction()?;
        for item in next.values().filter(|item| !item.entry.is_folder) {
            if previous_by_id
                .get(item.entry.file_id.as_str())
                .is_some_and(|old| old.entry.path != item.entry.path)
            {
                tx.execute(
                    "UPDATE files SET path=?2 WHERE id=?1",
                    params![
                        item.entry.file_id,
                        format!(".ainote/reconcile/{}", item.entry.file_id),
                    ],
                )?;
            }
        }
        for item in next.values().filter(|item| !item.entry.is_folder) {
            let entry = &item.entry;
            let old = previous_by_id.get(entry.file_id.as_str());
            if previous
                .get(&entry.path)
                .is_some_and(|occupant| occupant.entry.file_id != entry.file_id)
            {
                tx.execute(
                    "UPDATE files SET path='.ainote/retired/' || id,
                    revision=revision+(deleted=0),deleted=1 WHERE path=?1 AND id<>?2",
                    params![entry.path, entry.file_id],
                )?;
            }
            if old.is_none_or(|old| {
                old.entry.path != entry.path || old.entry.hash != entry.hash || old.entry.deleted
            }) {
                tx.execute(
                    "INSERT INTO files VALUES (?1,?2,?3,?4,0) ON CONFLICT(id) DO UPDATE
                 SET path=excluded.path,hash=excluded.hash,revision=excluded.revision,deleted=0
                 WHERE files.path<>excluded.path OR files.hash<>excluded.hash
                    OR files.revision<>excluded.revision OR files.deleted<>0",
                    params![entry.file_id, entry.path, entry.hash, entry.revision],
                )?;
            }
            let identity = item.stamp.as_ref().and_then(|s| s.identity.as_ref());
            if old.and_then(|old| old.identity.as_ref()) != identity {
                if let Some(identity) = identity {
                    tx.execute(
                        "INSERT INTO file_disk_identity VALUES (?1,?2) ON CONFLICT(file_id)
                     DO UPDATE SET identity=excluded.identity WHERE identity<>excluded.identity",
                        params![entry.file_id, identity],
                    )?;
                } else {
                    tx.execute(
                        "DELETE FROM file_disk_identity WHERE file_id=?1",
                        [&entry.file_id],
                    )?;
                }
            }
        }
        for (path, old) in &previous {
            if !old.entry.deleted
                && !assigned.contains(&old.entry.file_id)
                && ((enumerated && supported(path)) || self.cache.entries.contains_key(path))
            {
                tx.execute(
                    "UPDATE files SET deleted=1,revision=revision+1 WHERE id=?1 AND deleted=0",
                    [&old.entry.file_id],
                )?;
            }
        }
        tx.commit()?;
        Ok(())
    }
}

#[cfg(all(test, windows))]
mod tests {
    use super::*;
    use std::path::PathBuf;

    fn previous(path: &str, native: usize, birth: usize) -> Previous {
        Previous {
            entry: Entry {
                file_id: format!("file-{native}"),
                path: path.to_owned(),
                hash: String::new(),
                revision: 1,
                deleted: false,
                is_folder: false,
                updated_at: None,
            },
            identity: Some(format!("1:0:{native}:0:{birth}")),
        }
    }

    #[test]
    fn fresh_native_identities_skip_all_canonical_destination_queries() {
        let previous: BTreeMap<_, _> = (0..2048)
            .map(|index| {
                let path = format!("old-{index}.md");
                (path.clone(), previous(&path, index, 1))
            })
            .collect();
        let mut identities = WindowsIdentityIndex::new(&previous);
        let mut queries = 0;
        for index in 2048..6144 {
            let found = identities
                .tunneled_identity(
                    &format!("1:0:{index}:0:2"),
                    &format!("new-{index}.md"),
                    |_| {
                        queries += 1;
                        Ok(None)
                    },
                )
                .unwrap();
            assert!(found.is_none());
        }
        assert_eq!(queries, 0);
        assert!(identities.destinations.is_none());
    }

    #[test]
    fn batch_case_aliases_build_the_canonical_destination_index_only_once() {
        const COUNT: usize = 1024;
        let mut previous = BTreeMap::new();
        for index in 0..COUNT {
            for (path, native, birth) in [
                (format!("source-{index}.md"), index, 1),
                (format!("TARGET-{index}.md"), index + COUNT, 2),
            ] {
                previous.insert(path.clone(), self::previous(&path, native, birth));
            }
        }
        let mut identities = WindowsIdentityIndex::new(&previous);
        let mut queries: BTreeMap<String, usize> = BTreeMap::new();
        for index in 0..COUNT {
            let found = identities
                .tunneled_identity(
                    &format!("1:0:{index}:0:2"),
                    &format!("target-{index}.md"),
                    |path| {
                        *queries.entry(path.to_owned()).or_default() += 1;
                        Ok((!path.starts_with("source-"))
                            .then(|| PathBuf::from(path.to_ascii_lowercase())))
                    },
                )
                .unwrap();
            assert_eq!(found, Some(format!("1:0:{index}:0:1").as_str()));
        }
        assert_eq!(queries.len(), COUNT * 3);
        assert!(queries.values().all(|count| *count == 1));
    }

    #[test]
    fn canonical_index_keeps_case_sensitive_destinations_and_ambiguous_sources_distinct() {
        let mut previous = BTreeMap::from([
            ("a.md".into(), previous("a.md", 1, 1)),
            ("B.md".into(), previous("B.md", 2, 2)),
        ]);
        let mut identities = WindowsIdentityIndex::new(&previous);
        // A case-sensitive filesystem resolves these to different destinations.
        assert!(identities
            .tunneled_identity("1:0:1:0:2", "b.md", |path| Ok(Some(PathBuf::from(path))))
            .unwrap()
            .is_none());
        assert_eq!(
            identities
                .tunneled_identity("1:0:1:0:2", "B.md", |_| unreachable!())
                .unwrap(),
            Some("1:0:1:0:1")
        );
        previous.insert("hard-link.md".into(), self::previous("hard-link.md", 1, 1));
        let mut identities = WindowsIdentityIndex::new(&previous);
        assert!(identities
            .tunneled_identity("1:0:1:0:2", "b.md", |_| unreachable!())
            .unwrap()
            .is_none());
        assert!(identities.destinations.is_none());
    }

    #[test]
    fn canonical_destination_index_skips_internal_retired_identity_metadata() {
        let mut retired = previous(".ainote/retired/old-id", 3, 3);
        retired.entry.deleted = true;
        let previous = BTreeMap::from([
            ("a.md".into(), previous("a.md", 1, 1)),
            ("B.md".into(), previous("B.md", 2, 2)),
            (retired.entry.path.clone(), retired),
        ]);
        let mut identities = WindowsIdentityIndex::new(&previous);
        let found = identities
            .tunneled_identity("1:0:1:0:2", "b.md", |path| {
                // Workspace::resolve rejects internal metadata paths. They are
                // tombstone bookkeeping, never candidate public destinations.
                if path.starts_with(".ainote/") {
                    return Err(HostError::new("UNSAFE_PATH"));
                }
                Ok(Some(PathBuf::from(path.to_ascii_lowercase())))
            })
            .unwrap();
        assert_eq!(found, Some("1:0:1:0:1"));
    }
}
