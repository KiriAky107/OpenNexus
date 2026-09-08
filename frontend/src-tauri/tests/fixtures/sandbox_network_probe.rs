//! Standalone native test probe; never shipped or used to launch extensions.
use std::net::{SocketAddr, TcpStream, UdpSocket};
use std::time::Duration;
fn main() {
    let args: Vec<_> = std::env::args().collect();
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
