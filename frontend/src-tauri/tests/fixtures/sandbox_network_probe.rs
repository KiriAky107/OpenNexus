//! 独立本机测试探针；从未发货或用于启动扩展。
use std::net::{SocketAddr, TcpStream, UdpSocket};
use std::time::Duration;
fn main() {
    let args: Vec<_> = std::env::args().collect();
    if args.get(1).is_some_and(|value| value == "file_denied_100") {
        for path in &args[2..] {
            for _ in 0..100 {
                if std::fs::File::open(path).is_ok()
                    || std::fs::OpenOptions::new().write(true).open(path).is_ok()
                {
                    std::process::exit(88);
                }
            }
        }
        std::process::exit(0);
    }
    if args.get(1).is_some_and(|s| s == "cpu_burn") {
        std::thread::scope(|scope| {
            for _ in 0..8 {
                scope.spawn(|| {
                    let until = std::time::Instant::now() + Duration::from_secs(120);
                    let mut value = 1u64;
                    while std::time::Instant::now() < until {
                        for _ in 0..10000 {
                            value = std::hint::black_box(value.wrapping_mul(6364136223846793005).wrapping_add(1));
                        }
                    }
                });
            }
        });
        return;
    }
    if args.get(1).is_some_and(|s| s.starts_with("mcp")) {
        use std::io::{BufRead, Write};
        fn read(reader: &mut impl BufRead) -> String { let mut line = String::new(); reader.read_line(&mut line).unwrap(); line }
        fn id(line: &str) -> &str { line.split("\"id\":\"").nth(1).unwrap().split('"').next().unwrap() }
        fn reply(id: &str, result: &str) { println!("{{\"jsonrpc\":\"2.0\",\"id\":\"{id}\",\"result\":{result}}}"); std::io::stdout().flush().unwrap(); }
        let mut input = std::io::stdin().lock();
        let request = read(&mut input);
        assert!(request.contains("\"method\":\"initialize\""));
        reply(id(&request), if args[1] == "mcp_bad_version" { r#"{"protocolVersion":"unknown","capabilities":{},"serverInfo":{"name":"fixture","version":"1"}}"# } else { r#"{"protocolVersion":"2025-11-25","capabilities":{"tools":{}},"serverInfo":{"name":"fixture","version":"1"}}"# });
        assert!(read(&mut input).contains("notifications/initialized"));
        let request = read(&mut input);
        assert!(request.contains("tools/list"));
        if args[1] == "mcp_pages" {
            reply(id(&request), r#"{"tools":[],"nextCursor":"second"}"#);
            let page = read(&mut input);
            assert!(page.contains("tools/list") && page.contains("second"));
            reply(id(&page), r#"{"tools":[{"name":"echo","inputSchema":{"type":"object","additionalProperties":false},"outputSchema":{"type":"object","required":["ok"],"properties":{"ok":{"type":"boolean"}}}}]}"#);
        } else {
        reply(id(&request), r#"{"tools":[{"name":"echo","inputSchema":{"type":"object","additionalProperties":false},"outputSchema":{"type":"object","required":["ok"],"properties":{"ok":{"type":"boolean"}}}}]}"#);
        }
        let mut request = read(&mut input);
        assert!(request.contains("tools/call"));
        if args[1] == "mcp_memory" {
            let mut excessive = Vec::<u8>::new();
            let _ = excessive.try_reserve_exact(600 * 1024 * 1024);
            std::thread::sleep(Duration::from_secs(120));
            return;
        }
        if args[1] == "mcp_processes" {
            let mut children = Vec::new();
            for _ in 0..24 {
                match std::process::Command::new(std::env::current_exe().unwrap()).arg("wait").spawn() {
                    Ok(child) => children.push(child),
                    Err(_) => break,
                }
            }
            std::thread::sleep(Duration::from_secs(120));
            for mut child in children { let _ = child.kill(); let _ = child.wait(); }
            return;
        }
        if args[1] == "mcp_scratch" {
            let scratch = std::path::PathBuf::from(std::env::var_os("TEMP").unwrap());
            let file = std::fs::File::create(scratch.join("quota-probe.bin")).unwrap();
            file.set_len(300 * 1024 * 1024).unwrap();
            std::thread::sleep(Duration::from_secs(120));
            return;
        }
        if args[1] == "mcp_cancel" || args[1] == "mcp_deadline" || args[1] == "mcp_cpu" {
            let _child = std::process::Command::new(std::env::current_exe().unwrap()).arg(if args[1] == "mcp_cpu" { "cpu_burn" } else { "wait" }).spawn().unwrap();
            std::thread::sleep(Duration::from_secs(120));
            return;
        }
        if args[1] == "mcp_remote_error" {
            println!("{{\"jsonrpc\":\"2.0\",\"id\":\"{}\",\"error\":{{\"code\":-32602,\"message\":\"fixture error\"}}}}", id(&request));
            std::io::stdout().flush().unwrap();
            request = read(&mut input);
            assert!(request.contains("tools/call"));
        }
        if args[1] == "mcp_network_denied" {
            println!("{}", r#"{"jsonrpc":"2.0","id":"network-request","method":"opennexus/network.fetch","params":{"url":"https://127.0.0.1/","method":"GET"}}"#);
            std::io::stdout().flush().unwrap();
            let response = read(&mut input);
            assert!(response.contains("network-request") && response.contains("EXTENSION_NETWORK_ADDRESS_DENIED"));
        }
        println!(r#"{{"jsonrpc":"2.0","id":"server-ping","method":"ping"}}"#);
        std::io::stdout().flush().unwrap();
        let ping = read(&mut input);
        assert!(ping.contains("server-ping") && ping.contains("result"));
        if args[1] != "mcp_idle_change" && args[1] != "mcp_twenty" { println!(r#"{{"jsonrpc":"2.0","method":"notifications/tools/list_changed"}}"#); }
        reply(if args[1] == "mcp_wrong_id" { "wrong-request" } else { id(&request) }, if args[1] == "mcp_bad_result" { r#"{"content":[],"structuredContent":{"ok":"wrong type"}}"# } else { r#"{"content":[{"type":"text","text":"native MCP success"}],"structuredContent":{"ok":true}}"# });
        if args[1] == "mcp_twenty" {
            for _ in 1..20 {
                let request = read(&mut input);
                assert!(request.contains("tools/call"));
                reply(id(&request), r#"{"content":[{"type":"text","text":"native MCP success"}],"structuredContent":{"ok":true}}"#);
            }
        }
        if args[1] == "mcp_idle_change" {
            std::thread::sleep(Duration::from_millis(50));
            println!(r#"{{"jsonrpc":"2.0","method":"notifications/tools/list_changed"}}"#);
            std::io::stdout().flush().unwrap();
            let stale = read(&mut input);
            if !stale.is_empty() { reply(id(&stale), r#"{"content":[],"structuredContent":{"ok":true}}"#); }
            return;
        }
        // MCP 服务器在调用之间保持活动状态，直到 Host 关闭标准输入。
        while !read(&mut input).is_empty() {}
        return;
    }
    if args.get(1).is_some_and(|s| s == "stderr_flood" || s == "stdout_flood") {
        use std::io::Write;
        let _child = std::process::Command::new(std::env::current_exe().unwrap()).arg("wait").spawn().unwrap();
        if args[1] == "stderr_flood" { std::io::stderr().write_all(&vec![b'x'; 2 * 1024 * 1024]).unwrap(); }
        else { std::io::stdout().write_all(&vec![b'x'; 2 * 1024 * 1024 + 1]).unwrap(); }
        std::thread::sleep(Duration::from_secs(120));
        return;
    }
    if args.get(1).is_some_and(|s| s == "file_rpc") {
        use std::io::{Read, Write};
        #[repr(C)]
        struct FileIdentity { volume: u64, id: [u8; 16] }
        #[link(name = "kernel32")]
        extern "system" {
            fn GetFileInformationByHandleEx(handle: *mut std::ffi::c_void,
                class: i32, info: *mut std::ffi::c_void, size: u32) -> i32;
        }
        let sentinel: usize = args[2].parse().unwrap();
        let mut identity = FileIdentity { volume: 0, id: [0; 16] };
        // 数字句柄可能会为不相关的子对象起别名。比较实际文件标识，而不读取可能有别名的管道句柄。
        if unsafe { GetFileInformationByHandleEx(sentinel as *mut _, 18,
            (&mut identity as *mut FileIdentity).cast(),
            std::mem::size_of::<FileIdentity>() as u32) } != 0 {
            let hex: String = identity.id.iter().map(|b| format!("{b:02x}")).collect();
            if format!("{}:{}", identity.volume, hex) == args[3] {
                std::process::exit(85);
            }
        }
        println!("{{\"method\":\"notes.read\",\"path\":\"fixture.md\"}}");
        std::io::stdout().flush().unwrap();
        let mut response = String::new();
        std::io::stdin().take(4096).read_to_string(&mut response).unwrap();
        if !response.contains("\"content\":\"from host broker\"") { std::process::exit(86); }
        println!("{{\"ok\":true}}");
        eprintln!("fixture diagnostic");
        return;
    }
    if args.get(1).is_some_and(|s| s == "wait_tree") {
        let mut child = std::process::Command::new(std::env::current_exe().unwrap())
            .arg("wait")
            .spawn()
            .unwrap();
        std::thread::sleep(Duration::from_secs(120));
        let _ = child.kill();
        let _ = child.wait();
        std::process::exit(84);
    }
    if args.get(1).is_some_and(|s| s == "child_udp_100") {
        for _ in 0..100 {
            let status = std::process::Command::new(std::env::current_exe().unwrap())
                .args(["udp", &args[2]])
                .status()
                .unwrap();
            if !matches!(status.code(), Some(0 | 77)) {
                std::process::exit(87);
            }
        }
        std::process::exit(0);
    }
    if args.get(1).is_some_and(|s| s == "wait") {
        std::thread::sleep(Duration::from_secs(120));
        std::process::exit(84);
    }
    if args.get(1).is_some_and(|s| s == "launch") {
        let mut expected = [
            "launch",
            "",
            "space value",
            "引号🦀",
            "trailing\\",
            "a\"b",
            "slash\\\"quote",
            "&|%PATH%",
            "line\nbreak",
        ]
        .into_iter()
        .map(str::to_owned)
        .collect::<Vec<_>>();
        expected.extend((8..100).map(|index| {
            format!(r#"attack-{index} & | < > ^ %COMSPEC% $(echo injected) \" \\"#)
        }));
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
