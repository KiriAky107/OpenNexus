//! Standalone native test probe; never shipped or used to launch extensions.
use std::net::{SocketAddr, TcpStream, UdpSocket};
use std::time::Duration;
fn main() {
    let args: Vec<_> = std::env::args().collect();
    if args.get(1).is_some_and(|s| s == "wait") {
        std::thread::sleep(Duration::from_secs(60));
        std::process::exit(84);
    }
    if args.get(1).is_some_and(|s| s == "launch") {
        let expected = [
            "launch",
            "",
            "space value",
            "引号🦀",
            "trailing\\",
            "a\"b",
            "slash\\\"quote",
            "&|%PATH%",
            "line\nbreak",
        ];
        if args[1..] != expected {
            std::process::exit(82);
        }
        let environment: std::collections::BTreeMap<_, _> = std::env::vars_os().collect();
        let names: std::collections::BTreeSet<_> =
            environment.keys().map(|k| k.to_str().unwrap()).collect();
        if names
            != ["CUSTOM", "LOCALAPPDATA", "SYSTEMROOT", "TEMP", "TMP"]
                .into_iter()
                .collect()
            || std::env::var("CUSTOM").as_deref() != Ok("declared=值")
            || std::env::var_os("TEMP") != std::env::var_os("TMP")
            || std::path::PathBuf::from(std::env::var_os("LOCALAPPDATA").unwrap()).join("Temp")
                != std::path::PathBuf::from(std::env::var_os("TEMP").unwrap())
        {
            std::process::exit(83);
        }
        std::process::exit(0);
    }
    if args.len() != 3 {
        std::process::exit(79);
    }
    let address: SocketAddr = args[2].parse().unwrap();
    let result = match args[1].as_str() {
        "tcp" => TcpStream::connect_timeout(&address, Duration::from_secs(2)).map(|_| ()),
        "udp" => UdpSocket::bind(if address.is_ipv4() {
            "0.0.0.0:0"
        } else {
            "[::]:0"
        })
        .and_then(|socket| socket.send_to(b"probe", address))
        .map(|_| ()),
        _ => std::process::exit(79),
    };
    std::process::exit(match result {
        Ok(()) => 0,
        Err(error) if error.raw_os_error() == Some(10013) => 77,
        Err(error) if error.kind() == std::io::ErrorKind::TimedOut => 80,
        Err(_) => 81,
    });
}
