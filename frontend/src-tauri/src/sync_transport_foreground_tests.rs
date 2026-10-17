use super::*;
use crate::mobile_sync_foreground::Foreground;
use std::io::Write;

#[tokio::test]
async fn sync_transport_retained_original_client_sends_nothing_after_stop_or_restart() {
    let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
    listener.set_nonblocking(true).unwrap();
    let endpoint = format!("http://{}/", listener.local_addr().unwrap());
    let foreground = Foreground::default();
    foreground.on_start();
    let client = SyncClient::new(&endpoint, Zeroizing::new("owned-only".into()), true)
        .unwrap()
        .with_foreground(foreground.lease().unwrap());
    foreground.on_stop();
    assert_eq!(
        client
            .json(Method::GET, "sync/v1/handshake?protocol=1", None)
            .await
            .unwrap_err()
            .code,
        "SYNC_FOREGROUND_CHANGED"
    );
    foreground.on_start();
    assert_eq!(
        client
            .json(Method::GET, "sync/v1/handshake?protocol=1", None)
            .await
            .unwrap_err()
            .code,
        "SYNC_FOREGROUND_CHANGED"
    );
    assert_eq!(
        listener.accept().unwrap_err().kind(),
        std::io::ErrorKind::WouldBlock
    );
}

#[tokio::test]
async fn sync_transport_blocking_io_yields_to_native_stop_and_refuses_late_worker_result() {
    let foreground = Foreground::default();
    foreground.on_start();
    let client = SyncClient::new(
        "https://sync.example/",
        Zeroizing::new(String::new()),
        false,
    )
    .unwrap()
    .with_foreground(foreground.lease().unwrap());
    let (entered, ready) = tokio::sync::oneshot::channel();
    let release = Arc::new(std::sync::Barrier::new(2));
    let held = release.clone();
    let work = tokio::spawn(async move {
        client
            .io(move |check| {
                check()?;
                entered.send(()).unwrap();
                held.wait();
                check()?;
                Ok(())
            })
            .await
    });
    tokio::time::timeout(Duration::from_secs(5), ready)
        .await
        .unwrap()
        .unwrap();
    let native = foreground.clone();
    let (stopped, notice) = std::sync::mpsc::channel();
    let activity = std::thread::spawn(move || {
        native.on_stop();
        stopped.send(()).unwrap();
    });
    let returned = notice.recv_timeout(Duration::from_secs(2)).is_ok();
    release.wait();
    activity.join().unwrap();
    assert!(
        returned,
        "Owned IO worker retained a lifecycle read guard across file IO"
    );
    assert_eq!(
        work.await.unwrap().unwrap_err().code,
        "SYNC_FOREGROUND_CHANGED"
    );
}

#[tokio::test]
async fn sync_transport_native_stop_cancels_original_held_http_body_without_retry() {
    let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
    let endpoint = format!("http://{}/", listener.local_addr().unwrap());
    let (entered, ready) = tokio::sync::oneshot::channel();
    let (release, held) = std::sync::mpsc::channel();
    let server = std::thread::spawn(move || {
        let (mut stream, _) = listener.accept().unwrap();
        stream
            .set_read_timeout(Some(Duration::from_secs(5)))
            .unwrap();
        let mut request = Vec::new();
        while !request.ends_with(b"\r\n\r\n") {
            let mut byte = [0];
            assert_eq!(stream.read(&mut byte).unwrap(), 1);
            request.push(byte[0]);
            assert!(request.len() < 8192);
        }
        stream.write_all(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 11\r\nConnection: close\r\n\r\n").unwrap();
        entered.send(()).unwrap();
        held.recv_timeout(Duration::from_secs(5)).unwrap();
        let _ = stream.write_all(b"{\"ok\":true}");
        request
    });
    let foreground = Foreground::default();
    foreground.on_start();
    let client = SyncClient::new(&endpoint, Zeroizing::new("owned-only".into()), true)
        .unwrap()
        .with_foreground(foreground.lease().unwrap());
    let request = tokio::spawn(async move {
        client
            .json(Method::GET, "sync/v1/handshake?protocol=1", None)
            .await
    });
    tokio::time::timeout(Duration::from_secs(5), ready)
        .await
        .unwrap()
        .unwrap();
    foreground.on_stop();
    let outcome = tokio::time::timeout(Duration::from_secs(5), request)
        .await
        .unwrap()
        .unwrap();
    release.send(()).unwrap();
    let original = server.join().unwrap();
    assert_eq!(outcome.unwrap_err().code, "SYNC_FOREGROUND_CHANGED");
    assert!(original.starts_with(b"GET /sync/v1/handshake?protocol=1 HTTP/1.1\r\n"));
}
