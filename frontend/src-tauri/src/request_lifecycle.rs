//! Reserve before dispatch so cancellation cannot race a delayed IPC invocation.
use std::{
    collections::HashMap,
    future::Future,
    sync::{Arc, Mutex},
    time::{Duration, Instant},
};
use tokio::sync::watch;

struct Entry {
    deadline: Instant,
    claimed: bool,
    cancel: watch::Sender<bool>,
}
#[derive(Default, Clone)]
pub struct Requests {
    entries: Arc<Mutex<HashMap<String, Entry>>>,
}
pub struct Lease {
    id: String,
    owner: Requests,
    deadline: Instant,
    cancel: watch::Receiver<bool>,
}

impl Requests {
    pub fn prepare(&self, timeout_ms: u64) -> Result<String, String> {
        if !(1..=600_000).contains(&timeout_ms) {
            return Err("CORE_TIMEOUT_INVALID".into());
        }
        let mut entries = self.entries.lock().map_err(|_| "HOST_BUSY")?;
        entries.retain(|_, entry| entry.claimed || entry.deadline > Instant::now());
        if entries.len() >= 64 {
            return Err("CORE_REQUEST_LIMIT".into());
        }
        let id = uuid::Uuid::new_v4().to_string();
        let (cancel, _) = watch::channel(false);
        entries.insert(
            id.clone(),
            Entry {
                deadline: Instant::now() + Duration::from_millis(timeout_ms),
                claimed: false,
                cancel,
            },
        );
        Ok(id)
    }
    pub fn claim(&self, id: &str) -> Result<Lease, String> {
        let mut entries = self.entries.lock().map_err(|_| "HOST_BUSY")?;
        let entry = entries.get_mut(id).ok_or("CORE_REQUEST_NOT_PREPARED")?;
        if entry.claimed {
            return Err("CORE_REQUEST_ALREADY_STARTED".into());
        }
        if entry.deadline <= Instant::now() {
            entries.remove(id);
            return Err("REQUEST_TIMEOUT".into());
        }
        entry.claimed = true;
        Ok(Lease {
            id: id.into(),
            owner: self.clone(),
            deadline: entry.deadline,
            cancel: entry.cancel.subscribe(),
        })
    }
    pub fn cancel(&self, id: &str) -> Result<(), String> {
        if let Some(entry) = self.entries.lock().map_err(|_| "HOST_BUSY")?.remove(id) {
            entry.cancel.send_replace(true);
        }
        Ok(())
    }
}
impl Lease {
    /// Recheck after synchronous encoding/validation, immediately before network IO.
    pub fn checkpoint(&self) -> impl Fn() -> Result<(), String> + Send + 'static {
        let cancel = self.cancel.clone();
        let deadline = self.deadline;
        move || {
            if *cancel.borrow() {
                return Err("REQUEST_CANCELLED".into());
            }
            if deadline <= Instant::now() {
                return Err("REQUEST_TIMEOUT".into());
            }
            Ok(())
        }
    }
    pub async fn run<T>(
        &mut self,
        operation: impl Future<Output = Result<T, String>>,
    ) -> Result<T, String> {
        // Check current state before polling an operation with possible side effects.
        if *self.cancel.borrow() {
            return Err("REQUEST_CANCELLED".into());
        }
        if self.deadline <= Instant::now() {
            return Err("REQUEST_TIMEOUT".into());
        }
        tokio::select! {
            biased;
            _ = self.cancel.changed() => Err("REQUEST_CANCELLED".into()),
            _ = tokio::time::sleep_until(self.deadline.into()) => Err("REQUEST_TIMEOUT".into()),
            value = operation => value,
        }
    }
}
impl Drop for Lease {
    fn drop(&mut self) {
        if let Ok(mut entries) = self.owner.entries.lock() {
            entries.remove(&self.id);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[tokio::test]
    async fn cancellation_before_dispatch_and_replay_never_run_work() {
        let requests = Requests::default();
        let id = requests.prepare(1000).unwrap();
        requests.cancel(&id).unwrap();
        assert!(requests.claim(&id).is_err());
        let id = requests.prepare(1000).unwrap();
        let mut lease = requests.claim(&id).unwrap();
        assert!(requests.claim(&id).is_err());
        requests.cancel(&id).unwrap();
        let result: Result<(), String> = lease
            .run(async { panic!("cancelled work was polled") })
            .await;
        assert_eq!(result.unwrap_err(), "REQUEST_CANCELLED");
    }
    #[tokio::test]
    async fn timeout_and_cancel_drop_inflight_work_and_release_capacity() {
        let requests = Requests::default();
        let id = requests.prepare(10).unwrap();
        let mut lease = requests.claim(&id).unwrap();
        assert_eq!(
            lease
                .run(std::future::pending::<Result<(), String>>())
                .await
                .unwrap_err(),
            "REQUEST_TIMEOUT"
        );
        drop(lease);
        assert!(requests.entries.lock().unwrap().is_empty());
        let id = requests.prepare(1000).unwrap();
        let mut lease = requests.claim(&id).unwrap();
        let (result, ()) = tokio::join!(
            lease.run(std::future::pending::<Result<(), String>>()),
            async {
                tokio::task::yield_now().await;
                requests.cancel(&id).unwrap();
            }
        );
        assert_eq!(result.unwrap_err(), "REQUEST_CANCELLED");
        drop(lease);
        assert!(requests.entries.lock().unwrap().is_empty());
    }
    #[test]
    fn capacity_and_expired_reservations_are_bounded() {
        let requests = Requests::default();
        for _ in 0..64 {
            requests.prepare(1000).unwrap();
        }
        assert_eq!(requests.prepare(1000).unwrap_err(), "CORE_REQUEST_LIMIT");
        for entry in requests.entries.lock().unwrap().values_mut() {
            entry.deadline = Instant::now();
        }
        assert!(requests.prepare(1000).is_ok());
    }
    #[tokio::test]
    async fn cancelling_a_real_response_closes_its_socket() {
        use std::io::{Read, Write};
        let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let endpoint = listener.local_addr().unwrap();
        let (started, received) = tokio::sync::oneshot::channel();
        let server = std::thread::spawn(move || {
            let (mut socket, _) = listener.accept().unwrap();
            socket
                .set_read_timeout(Some(Duration::from_secs(5)))
                .unwrap();
            let mut request = Vec::new();
            while !request.ends_with(b"\r\n\r\n") {
                let mut byte = [0];
                socket.read_exact(&mut byte).unwrap();
                request.push(byte[0]);
            }
            socket
                .write_all(
                    b"HTTP/1.1 200 OK\r\nContent-Length: 1000000\r\nConnection: close\r\n\r\nx",
                )
                .unwrap();
            started.send(()).unwrap();
            match socket.read(&mut [0]) {
                Ok(0) => true,
                Err(error) => matches!(
                    error.kind(),
                    std::io::ErrorKind::ConnectionReset | std::io::ErrorKind::ConnectionAborted
                ),
                _ => false,
            }
        });
        let requests = Requests::default();
        let id = requests.prepare(10000).unwrap();
        let mut lease = requests.claim(&id).unwrap();
        let operation = async {
            let mut response = reqwest::Client::builder()
                .no_proxy()
                .build()
                .unwrap()
                .get(format!("http://{endpoint}/"))
                .send()
                .await
                .map_err(|_| "HTTP_FAILURE".to_string())?;
            while response
                .chunk()
                .await
                .map_err(|_| "BODY_FAILURE".to_string())?
                .is_some()
            {}
            Ok(())
        };
        let (result, ()) = tokio::join!(lease.run(operation), async {
            received.await.unwrap();
            requests.cancel(&id).unwrap();
        });
        assert_eq!(result.unwrap_err(), "REQUEST_CANCELLED");
        assert!(
            tokio::task::spawn_blocking(move || server.join().unwrap())
                .await
                .unwrap(),
            "HTTP socket was not closed on cancellation"
        );
    }
}
