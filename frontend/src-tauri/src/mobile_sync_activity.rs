//! Native RAM admissions for foreground cycles, separate from durable file
//! operations and receipts. No token, session or lifecycle capability is IPC data.
use crate::{
    mobile_sync_cycle::{Request, Target},
    mobile_sync_foreground::Lease,
    sync_client::SyncError,
    sync_progress::{Activity, Event},
};
use serde::{Deserialize, Serialize};
use std::{
    collections::{BTreeMap, BTreeSet},
    sync::{Arc, Mutex, MutexGuard},
};

type Result<T> = std::result::Result<T, SyncError>;
const MAX_ADMISSIONS: usize = 256;

#[derive(Clone, Serialize)]
pub struct Observation {
    pub target: Target,
    pub invocation_id: String,
    pub generation: u64,
    pub activity: Activity,
    pub error: Option<String>,
}

#[derive(Clone, Serialize)]
#[serde(tag = "state", content = "observation", rename_all = "snake_case")]
pub enum Snapshot {
    Inactive,
    Known(Box<Observation>),
}

#[derive(Clone, Copy, PartialEq, Eq, Debug, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Stop {
    Cancelled,
    Finished,
    Inactive,
}

struct Owner {
    lease: Lease,
    value: Observation,
}

#[derive(Default)]
struct State {
    generation: u64,
    exhausted: bool,
    current: Option<String>,
    records: BTreeMap<String, Owner>,
    // Clearing account metadata must not let an already admitted nonce run a
    // second time. These bounded tombstones contain only canonical random IDs.
    seen: BTreeSet<String>,
}

#[derive(Clone, Default)]
pub struct Store {
    state: Arc<Mutex<State>>,
}

pub enum Admission {
    Started(Box<Ticket>),
    Known(Snapshot),
}

/// The sole cycle IPC envelope. There is no WebView HTTP method/URL/header,
/// credential read, filesystem path or lifecycle capability field.
#[derive(Deserialize)]
#[serde(tag = "action", rename_all = "snake_case", deny_unknown_fields)]
pub enum Command {
    Run {
        request: Request,
        invocation_id: Option<String>,
    },
    Activity {
        target: Target,
        invocation_id: String,
    },
    Stop {
        target: Target,
        invocation_id: String,
    },
}
impl Command {
    pub fn validate(&self) -> Result<()> {
        match self {
            Self::Run {
                request,
                invocation_id,
            } => {
                request.validate()?;
                match (request, invocation_id) {
                    (Request::Now { .. }, Some(id)) => invocation(id),
                    (Request::Now { .. }, None) | (_, Some(_)) => {
                        Err(SyncError::new("SYNC_ACTIVITY_REQUEST_INVALID"))
                    }
                    _ => Ok(()),
                }
            }
            Self::Activity {
                target,
                invocation_id,
            }
            | Self::Stop {
                target,
                invocation_id,
            } => {
                invocation(invocation_id)?;
                Request::Now {
                    target: target.clone(),
                }
                .validate()
            }
        }
    }
}

#[derive(Serialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum Reply {
    Result {
        response: Box<crate::mobile_sync_cycle::Response>,
    },
    Activity {
        snapshot: Snapshot,
    },
    Stopped {
        state: Stop,
    },
}

/// Nonserializable native ownership. Dropping an unfinished command interrupts
/// its original lease; it never cancels a replacement admission or identity.
pub struct Ticket {
    store: Store,
    lease: Lease,
    target: Target,
    invocation_id: String,
    generation: u64,
    settled: bool,
}

fn invocation(value: &str) -> Result<()> {
    if uuid::Uuid::parse_str(value).is_ok_and(|id| id.to_string() == value) {
        Ok(())
    } else {
        Err(SyncError::new("SYNC_ACTIVITY_ID_INVALID"))
    }
}

impl Store {
    fn lock(&self) -> Result<MutexGuard<'_, State>> {
        self.state
            .lock()
            .map_err(|_| SyncError::new("SYNC_ACTIVITY_UNAVAILABLE"))
    }

    /// Before waiting for the Auth gate. No lifecycle read/check/cancel occurs
    /// while holding this store's mutex: progress holds the reverse read order.
    pub fn admit(
        &self,
        lease: &Lease,
        target: Target,
        invocation_id: String,
        now: i64,
    ) -> Result<Admission> {
        Request::Now {
            target: target.clone(),
        }
        .validate()?;
        invocation(&invocation_id)?;
        if now < 0 {
            return Err(SyncError::new("SYNC_TIME_INVALID"));
        }
        for _ in 0..3 {
            lease.check()?;
            let (generation, previous) = {
                let state = self.lock()?;
                if state.exhausted {
                    return Err(SyncError::new("SYNC_ACTIVITY_LIMIT"));
                }
                if let Some(owner) = state.records.get(&invocation_id) {
                    if owner.value.target != target {
                        return Err(SyncError::new("SYNC_ACTIVITY_OWNER_CHANGED"));
                    }
                    drop(state);
                    return Ok(Admission::Known(self.snapshot(&target, &invocation_id)?));
                }
                if state.seen.contains(&invocation_id) {
                    return Err(SyncError::new("SYNC_ACTIVITY_RECONCILIATION_REQUIRED"));
                }
                let previous = state
                    .current
                    .as_ref()
                    .and_then(|id| state.records.get(id))
                    .filter(|owner| owner.value.activity.running)
                    .map(|owner| owner.lease.clone());
                (state.generation, previous)
            };
            let previous_running = previous.is_some_and(|lease| lease.check().is_ok());
            let admitted = lease.with_current(|| {
                let mut state = self.lock()?;
                if state.generation != generation {
                    return Ok(None);
                }
                if previous_running {
                    return Err(SyncError::new("SYNC_ACTIVITY_BUSY"));
                }
                if state.seen.len() >= MAX_ADMISSIONS {
                    return Err(SyncError::new("SYNC_ACTIVITY_LIMIT"));
                }
                let next = state
                    .generation
                    .checked_add(1)
                    .ok_or_else(|| SyncError::new("SYNC_ACTIVITY_LIMIT"))?;
                state.generation = next;
                state.current = Some(invocation_id.clone());
                state.seen.insert(invocation_id.clone());
                state.records.insert(
                    invocation_id.clone(),
                    Owner {
                        lease: lease.clone(),
                        value: Observation {
                            target: target.clone(),
                            invocation_id: invocation_id.clone(),
                            generation: next,
                            activity: Activity::start(next, now),
                            error: None,
                        },
                    },
                );
                Ok(Some(next))
            })?;
            if let Some(generation) = admitted {
                return Ok(Admission::Started(Box::new(Ticket {
                    store: self.clone(),
                    lease: lease.clone(),
                    target,
                    invocation_id,
                    generation,
                    settled: false,
                })));
            }
        }
        Err(SyncError::new("SYNC_ACTIVITY_BUSY"))
    }

    pub fn snapshot(&self, target: &Target, invocation_id: &str) -> Result<Snapshot> {
        invocation(invocation_id)?;
        let owner = {
            let state = self.lock()?;
            state
                .records
                .get(invocation_id)
                .filter(|owner| &owner.value.target == target)
                .map(|owner| (owner.lease.clone(), owner.value.clone()))
        };
        let Some((lease, value)) = owner else {
            return Ok(Snapshot::Inactive);
        };
        Ok(lease
            .with_current(|| Ok(Snapshot::Known(Box::new(value))))
            .unwrap_or(Snapshot::Inactive))
    }

    // This claim orders Stop against completion. The lifecycle CAS comes only
    // AFTER releasing the mutex, including when progress is waiting for it.
    fn claim_stop(
        &self,
        target: &Target,
        invocation_id: &str,
        now: i64,
    ) -> Result<(Stop, Option<Lease>)> {
        invocation(invocation_id)?;
        let mut state = self.lock()?;
        if state.current.as_deref() != Some(invocation_id) {
            return Ok((Stop::Inactive, None));
        }
        let Some(owner) = state.records.get_mut(invocation_id) else {
            return Ok((Stop::Inactive, None));
        };
        if &owner.value.target != target {
            return Ok((Stop::Inactive, None));
        }
        if !owner.value.activity.running {
            return Ok((Stop::Finished, Some(owner.lease.clone())));
        }
        owner
            .value
            .activity
            .finish(owner.value.generation, now, Some("SYNC_CANCELLED"));
        owner.value.error = Some("SYNC_CANCELLED".into());
        Ok((Stop::Cancelled, Some(owner.lease.clone())))
    }

    pub fn stop(&self, target: &Target, invocation_id: &str, now: i64) -> Result<Stop> {
        if now < 0 {
            return Err(SyncError::new("SYNC_TIME_INVALID"));
        }
        let (state, lease) = self.claim_stop(target, invocation_id, now)?;
        let Some(lease) = lease else {
            return Ok(Stop::Inactive);
        };
        let result = if state == Stop::Cancelled {
            lease.cancel_original()
        } else {
            lease.check()
        };
        Ok(if result.is_ok() {
            state
        } else {
            Stop::Inactive
        })
    }

    /// Call after native identity invalidation. No account/path/activity metadata
    /// survives, but old admitted IDs remain refused until this process exits.
    pub fn clear_identity(&self) -> Result<()> {
        let mut state = self.lock()?;
        state.records.clear();
        state.current = None;
        if let Some(next) = state.generation.checked_add(1) {
            state.generation = next;
        } else {
            state.exhausted = true;
        }
        Ok(())
    }

    fn observe(&self, invocation_id: &str, generation: u64, event: Event) -> Option<Snapshot> {
        let mut state = self.lock().ok()?;
        if state.current.as_deref() != Some(invocation_id) {
            return None;
        }
        let owner = state.records.get_mut(invocation_id)?;
        if owner.value.generation != generation || !owner.value.activity.running {
            return None;
        }
        owner.value.activity.observe(generation, event);
        Some(Snapshot::Known(Box::new(owner.value.clone())))
    }
}

impl Ticket {
    /// Collector for mobile_sync_cycle::execute, which already wraps delivery
    /// in the original Lease::progress. Do not wrap it again: a queued lifecycle
    /// writer and nested read locks could deadlock. This closure only takes RAM.
    pub fn collector(
        &self,
        deliver: Arc<dyn Fn(Snapshot) + Send + Sync>,
    ) -> Arc<dyn Fn(Event) + Send + Sync> {
        let store = self.store.clone();
        let id = self.invocation_id.clone();
        let generation = self.generation;
        Arc::new(move |event| {
            if let Some(value) = store.observe(&id, generation, event) {
                // Store lock was released before the main-WebView emission.
                deliver(value);
            }
        })
    }

    pub fn finish(&mut self, now: i64, error: Option<&str>) -> Result<Snapshot> {
        if now < 0 {
            return Err(SyncError::new("SYNC_TIME_INVALID"));
        }
        let current = self.lease.check().is_ok();
        let error = if current {
            error
        } else {
            Some("SYNC_CANCELLED")
        };
        {
            let mut state = self.store.lock()?;
            if state.current.as_deref() == Some(self.invocation_id.as_str()) {
                if let Some(owner) = state.records.get_mut(&self.invocation_id) {
                    if owner.value.generation == self.generation && owner.value.activity.running {
                        owner.value.activity.finish(self.generation, now, error);
                        owner.value.error = error.map(|code| {
                            crate::mobile_sync_cycle::public_error(SyncError::new(code))
                        });
                    }
                }
            }
        }
        self.settled = true;
        self.store.snapshot(&self.target, &self.invocation_id)
    }
}

impl Drop for Ticket {
    fn drop(&mut self) {
        if !self.settled {
            // No wall-clock or file IO in Drop. Preserve the original start time
            // if interrupted before the wrapper can record a completion time.
            let now = self
                .store
                .lock()
                .ok()
                .and_then(|state| {
                    state
                        .records
                        .get(&self.invocation_id)
                        .and_then(|owner| owner.value.activity.started_at)
                })
                .unwrap_or(0);
            let _ = self.store.stop(&self.target, &self.invocation_id, now);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{
        mobile_sync_foreground::Foreground,
        mobile_sync_session::Selection,
        sync_progress::{Direction, Phase},
    };

    fn target() -> Target {
        Target {
            expected: Selection {
                revision: 1,
                endpoint: "https://sync.example/".into(),
                account: "owned-reader".into(),
                operation_id: uuid::Uuid::new_v4().to_string(),
                generation: 1,
            },
            vault_id: uuid::Uuid::new_v4().to_string(),
            binding_id: uuid::Uuid::new_v4().to_string(),
        }
    }
    fn start(store: &Store, lease: &Lease, target: &Target, id: &str) -> Box<Ticket> {
        match store.admit(lease, target.clone(), id.into(), 100).unwrap() {
            Admission::Started(ticket) => ticket,
            _ => panic!("An original admission was not started"),
        }
    }
    fn known(value: Snapshot) -> Observation {
        match value {
            Snapshot::Known(value) => *value,
            _ => panic!("Expected an authorized original observation"),
        }
    }
    fn foreground() -> (Foreground, Lease) {
        let foreground = Foreground::default();
        foreground.on_start();
        let lease = foreground.lease().unwrap();
        (foreground, lease)
    }

    #[test]
    fn native_sync_activity_admits_once_before_auth_wait_and_remembers_completed_nonce() {
        let (_foreground, lease) = foreground();
        let store = Store::default();
        let target = target();
        let id = uuid::Uuid::new_v4().to_string();
        let mut original = start(&store, &lease, &target, &id);
        for _ in 0..2 {
            assert!(matches!(
                store
                    .admit(&lease, target.clone(), id.clone(), 101)
                    .unwrap(),
                Admission::Known(_)
            ));
        }
        let another = uuid::Uuid::new_v4().to_string();
        assert_eq!(
            store
                .admit(&lease, target.clone(), another.clone(), 101)
                .err()
                .unwrap()
                .code,
            "SYNC_ACTIVITY_BUSY"
        );
        original.finish(102, None).unwrap();
        let mut next = start(&store, &lease, &target, &another);
        assert!(matches!(
            store
                .admit(&lease, target.clone(), id.clone(), 103)
                .unwrap(),
            Admission::Known(_)
        ));
        let old = known(store.snapshot(&target, &id).unwrap());
        assert!(!old.activity.running);
        assert_eq!(old.activity.phase, Phase::Complete);
        assert_ne!(old.generation, next.generation);
        next.finish(104, None).unwrap();
    }

    #[test]
    fn native_sync_activity_exact_owner_identity_clear_and_late_finish_cannot_replace_new_cycle() {
        let (foreground, lease) = foreground();
        let store = Store::default();
        let target = target();
        let id = uuid::Uuid::new_v4().to_string();
        let mut original = start(&store, &lease, &target, &id);
        let mut other = target.clone();
        other.expected.revision += 1;
        assert!(matches!(
            store.snapshot(&other, &id).unwrap(),
            Snapshot::Inactive
        ));
        assert_eq!(store.stop(&other, &id, 101).unwrap(), Stop::Inactive);
        assert!(lease.check().is_ok());
        assert_eq!(
            store
                .admit(&lease, other.clone(), id.clone(), 101)
                .err()
                .unwrap()
                .code,
            "SYNC_ACTIVITY_OWNER_CHANGED"
        );
        foreground.invalidate_identity();
        store.clear_identity().unwrap();
        let fresh = foreground.lease().unwrap();
        assert_eq!(
            store
                .admit(&fresh, target.clone(), id.clone(), 102)
                .err()
                .unwrap()
                .code,
            "SYNC_ACTIVITY_RECONCILIATION_REQUIRED"
        );
        let next_id = uuid::Uuid::new_v4().to_string();
        let mut next = start(&store, &fresh, &other, &next_id);
        assert!(matches!(
            original.finish(103, None).unwrap(),
            Snapshot::Inactive
        ));
        assert_eq!(store.stop(&target, &id, 104).unwrap(), Stop::Inactive);
        assert!(fresh.check().is_ok());
        assert!(
            known(store.snapshot(&other, &next_id).unwrap())
                .activity
                .running
        );
        next.finish(105, None).unwrap();
    }

    #[test]
    fn native_sync_activity_original_stop_blocks_late_progress_and_never_cancels_new_epoch() {
        let (foreground, lease) = foreground();
        let store = Store::default();
        let target = target();
        let id = uuid::Uuid::new_v4().to_string();
        let mut original = start(&store, &lease, &target, &id);
        let collected = Arc::new(Mutex::new(Vec::new()));
        let output = collected.clone();
        let report = lease.progress(
            original.collector(Arc::new(move |value| output.lock().unwrap().push(value))),
        );
        report(Event::Processed(Direction::Upload));
        assert_eq!(collected.lock().unwrap().len(), 1);
        assert_eq!(store.stop(&target, &id, 101).unwrap(), Stop::Cancelled);
        report(Event::Processed(Direction::Download));
        assert_eq!(collected.lock().unwrap().len(), 1);
        assert!(matches!(
            original.finish(102, None).unwrap(),
            Snapshot::Inactive
        ));
        let fresh = foreground.lease().unwrap();
        assert_eq!(store.stop(&target, &id, 103).unwrap(), Stop::Inactive);
        assert!(fresh.check().is_ok());
        let state = store.lock().unwrap();
        let value = &state.records[&id].value;
        assert_eq!(value.activity.phase, Phase::Cancelled);
        assert_eq!(value.activity.uploaded_revisions, 1);
        assert_eq!(value.activity.received_revisions, 0);
        assert_eq!(value.activity.finished_at, Some(101));
    }

    #[test]
    fn native_sync_activity_background_hides_paths_and_dropped_command_cancels_only_original() {
        let (foreground, lease) = foreground();
        let store = Store::default();
        let target = target();
        let id = uuid::Uuid::new_v4().to_string();
        let original = start(&store, &lease, &target, &id);
        foreground.on_stop();
        assert!(matches!(
            store.snapshot(&target, &id).unwrap(),
            Snapshot::Inactive
        ));
        foreground.on_start();
        let fresh = foreground.lease().unwrap();
        drop(original);
        assert!(fresh.check().is_ok());
        let next_id = uuid::Uuid::new_v4().to_string();
        let next = start(&store, &fresh, &target, &next_id);
        drop(next);
        assert_eq!(fresh.check().unwrap_err().code, "SYNC_FOREGROUND_CHANGED");
        assert!(matches!(
            store.snapshot(&target, &next_id).unwrap(),
            Snapshot::Inactive
        ));
    }

    #[test]
    fn native_sync_activity_bounds_ids_history_generation_and_public_errors_without_erasing_nonce()
    {
        let (_foreground, lease) = foreground();
        let store = Store::default();
        let target = target();
        for id in [
            "../private",
            "not-a-uuid",
            "",
            "AAAAAAAA-AAAA-AAAA-AAAA-AAAAAAAAAAAA",
        ] {
            assert_eq!(
                store
                    .admit(&lease, target.clone(), id.into(), 100)
                    .err()
                    .unwrap()
                    .code,
                "SYNC_ACTIVITY_ID_INVALID"
            );
        }
        let id = uuid::Uuid::new_v4().to_string();
        let mut ticket = start(&store, &lease, &target, &id);
        let value = known(ticket.finish(101, Some("secret/provider/path")).unwrap());
        assert_eq!(value.error.as_deref(), Some("SYNC_CONNECTION_FAILED"));
        assert_eq!(store.stop(&target, &id, 102).unwrap(), Stop::Finished);
        assert!(lease.check().is_ok());
        {
            let mut state = store.lock().unwrap();
            state.generation = u64::MAX;
        }
        assert_eq!(
            store
                .admit(
                    &lease,
                    target.clone(),
                    uuid::Uuid::new_v4().to_string(),
                    103
                )
                .err()
                .unwrap()
                .code,
            "SYNC_ACTIVITY_LIMIT"
        );
        store.clear_identity().unwrap();
        assert!(store.lock().unwrap().exhausted);
        assert!(store.lock().unwrap().seen.contains(&id));
        assert_eq!(
            store
                .admit(&lease, target, uuid::Uuid::new_v4().to_string(), 104)
                .err()
                .unwrap()
                .code,
            "SYNC_ACTIVITY_LIMIT"
        );
    }

    #[test]
    fn native_sync_activity_envelope_refuses_transport_fields_and_non_now_invocation_ids() {
        let target = serde_json::to_value(target()).unwrap();
        let id = uuid::Uuid::new_v4().to_string();
        let good = serde_json::json!({"action":"run","request":{"action":"now","target":target},"invocation_id":id});
        assert!(serde_json::from_value::<Command>(good.clone())
            .unwrap()
            .validate()
            .is_ok());
        for key in ["token", "headers", "url", "lease"] {
            let mut invalid = good.clone();
            invalid[key] = serde_json::json!("not accepted");
            assert!(serde_json::from_value::<Command>(invalid).is_err());
        }
        for value in [
            serde_json::json!({"action":"run","request":{"action":"now","target":target}}),
            serde_json::json!({"action":"run","request":{"action":"status","target":target,"after":0,"limit":20},"invocation_id":id}),
            serde_json::json!({"action":"stop","target":target,"invocation_id":"../private"}),
        ] {
            assert!(serde_json::from_value::<Command>(value)
                .unwrap()
                .validate()
                .is_err());
        }
    }

    #[test]
    fn native_sync_activity_history_limit_refuses_new_work_without_forgetting_known_ids() {
        let (_foreground, lease) = foreground();
        let store = Store::default();
        let target = target();
        let mut first = None;
        for _ in 0..MAX_ADMISSIONS {
            let id = uuid::Uuid::new_v4().to_string();
            let mut ticket = start(&store, &lease, &target, &id);
            ticket.finish(101, None).unwrap();
            first.get_or_insert(id);
        }
        assert_eq!(
            store
                .admit(
                    &lease,
                    target.clone(),
                    uuid::Uuid::new_v4().to_string(),
                    102
                )
                .err()
                .unwrap()
                .code,
            "SYNC_ACTIVITY_LIMIT"
        );
        assert!(matches!(
            store.admit(&lease, target, first.unwrap(), 102).unwrap(),
            Admission::Known(_)
        ));
        assert_eq!(store.lock().unwrap().records.len(), MAX_ADMISSIONS);
    }

    #[test]
    fn native_sync_activity_stop_releases_store_before_waiting_for_an_inflight_progress_reader() {
        let (_foreground, lease) = foreground();
        let store = Store::default();
        let target = target();
        let id = uuid::Uuid::new_v4().to_string();
        let mut ticket = start(&store, &lease, &target, &id);
        let collector = ticket.collector(Arc::new(|_| panic!("Claimed Stop must refuse progress")));
        let barrier = Arc::new(std::sync::Barrier::new(2));
        let reader_barrier = barrier.clone();
        let (entered, wait) = std::sync::mpsc::channel();
        let report = lease.progress(Arc::new(move |event| {
            entered.send(()).unwrap();
            reader_barrier.wait();
            collector(event);
        }));
        let reader = std::thread::spawn(move || report(Event::Processed(Direction::Upload)));
        wait.recv().unwrap();
        let (result, original) = store.claim_stop(&target, &id, 101).unwrap();
        assert_eq!(result, Stop::Cancelled);
        let stopper = std::thread::spawn(move || original.unwrap().cancel_original().unwrap());
        barrier.wait();
        reader.join().unwrap();
        stopper.join().unwrap();
        assert!(matches!(
            ticket.finish(102, None).unwrap(),
            Snapshot::Inactive
        ));
    }
}
