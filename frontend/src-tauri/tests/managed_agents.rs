#![cfg(feature = "desktop")]
//! Opt-in live acceptance through the actual Host/Core boundary. Credentials
//! remain in the existing broker; only resolve for the selected provider is allowed.
use notesagent_host::{core::CoreSupervisor, credentials::CredentialBroker, workspace::Workspace, workspace_broker};
use serde_json::{json, Value};
use std::{path::PathBuf, sync::{Arc, Mutex}, time::{Duration, Instant}};

async fn request(core: &mut CoreSupervisor, vault: &str, method: &str, path: &str, body: Option<Value>) -> Value {
    let session = core.request_session(path).unwrap();
    let mut req = reqwest::Client::builder().timeout(Duration::from_secs(180)).build().unwrap()
        .request(method.parse::<reqwest::Method>().unwrap(), session.url)
        .header("Authorization", session.authorization.as_str()).header("X-Core-Generation", session.generation)
        .header("X-OpenNexus-Vault", vault).header("X-Request-Id", uuid::Uuid::new_v4().to_string());
    if let Some(body) = body { req = req.json(&body); }
    let response = req.send().await.unwrap();
    assert!(response.status().is_success(), "acceptance endpoint failed: {path} status {}", response.status());
    if path == "/api/chat" {
        let text = response.text().await.unwrap();
        for line in text.lines().filter_map(|line| line.strip_prefix("data: ")) {
            if let Ok(event) = serde_json::from_str::<Value>(line) {
                let kind = event["event"].as_str().unwrap_or("");
                if !matches!(kind, "Token" | "Thinking" | "ThinkingDelta" | "TextDelta") {
                    println!("chat event: {} name={} code={} status={}", kind, event["data"]["name"], event["data"]["code"], event["data"]["status"]);
                }
            }
        }
        assert!(!text.contains("event: error"), "chat returned an error event (content withheld)");
        assert!(text.contains("agent.define") && text.contains("agent.collaborate"), "expected management tools were not called");
        return json!({"stream_validated": true});
    }
    response.json().await.unwrap()
}

#[tokio::test]
#[ignore = "Requires explicit user consent, configured provider and QA_LIVE_DATA_DIR"]
async fn configured_deepseek_chat_definitions_and_collaboration() {
    let data = PathBuf::from(std::env::var("QA_LIVE_DATA_DIR").expect("explicit opt-in required"));
    let db = rusqlite::Connection::open_with_flags(data.join("core-data/app.db"), rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY).unwrap();
    let mut statement = db.prepare("SELECT config_json FROM provider_configs").unwrap();
    let config = statement.query_map([], |row| row.get::<_, String>(0)).unwrap().map(|row| serde_json::from_str::<Value>(&row.unwrap()).unwrap())
        .find(|item| item["base_url"].as_str().is_some_and(|url| url.trim_end_matches('/') == "https://api.deepseek.com") && item["enabled"] == true)
        .expect("No enabled official DeepSeek provider configured");
    let credential_id = config["credential_id"].as_str().unwrap().to_string();
    let credential_path = data.join("credentials/stronghold.v1");
    assert!(credential_path.is_file() && data.join("credentials/auto-unlock.dpapi").is_file(), "existing credential store required");
    let mut broker = CredentialBroker::new(credential_path);
    assert!(broker.ensure_system_unlock().expect("existing credential broker unavailable"));
    let broker = Arc::new(Mutex::new(broker));
    let temp = tempfile::Builder::new().prefix("opennexus-live-agents-").tempdir().unwrap();
    let root = temp.path().join("vault"); std::fs::create_dir(&root).unwrap();
    let workspace = Arc::new(Mutex::new(Workspace::open(&root).unwrap()));
    let vault = workspace.lock().unwrap().vault_id.clone();
    let backend = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../backend").canonicalize().unwrap();
    let (executable, args, cwd) = match std::env::var("QA_PACKAGED_CORE") {
        Ok(path) => { let path = PathBuf::from(path); (path.clone(), vec![], path.parent().unwrap().to_path_buf()) },
        Err(_) => (backend.join(".venv/Scripts/python.exe"), vec!["-m".into(), "app.sidecar".into()], backend),
    };
    let handler = workspace.clone();
    let allowed_credential = credential_id.clone();
    let mut core = CoreSupervisor::new(executable, args, cwd, temp.path().join("core")).with_broker(Arc::new(move |value| {
        if value["rpc"].as_str().unwrap_or("").starts_with("credentials.") {
            if value["rpc"] != "credentials.resolve" || value["params"]["id"] != allowed_credential { return Err("QA_CREDENTIAL_SCOPE_DENIED".into()); }
            return broker.lock().unwrap().dispatch(value);
        }
        workspace_broker::dispatch(&mut handler.lock().unwrap(), value)
    }));
    let provider = request(&mut core, &vault, "POST", "/api/providers", Some(json!({
        "name":"Isolated DeepSeek acceptance", "provider_type":config["provider_type"], "base_url":config["base_url"],
        "default_model":config["default_model"], "credential_id":credential_id
    }))).await;
    let provider_id = provider["provider_id"].as_str().unwrap();
    let model = config["default_model"].as_str().unwrap();
    let prompt = format!("这是隔离验收。请在本轮调用 agent.define 创建两个可复用智能体，名称分别为 QA检查员、QA整理员，provider_id={provider_id}，model={model}，tools=[\"notes.list\"]，指令为只完成指定短任务并报告真实结果。随后调用 agent.collaborate 安排两人协作，第二人 depends_on 第一人：第一人用 notes.list 列出当前隔离测试知识库的笔记数量，第二人读取上游结果并再次用 notes.list 核对数量。每个成员最多100字，不修改笔记，不启动独立任务，只创建一份待确认分工，然后停止等待用户确认。不要只描述计划，要实际调用工具。每个配置 token_budget=8000，总预算24000。");
    request(&mut core, &vault, "POST", "/api/chat", Some(json!({"provider_id":provider_id,"model":model,"allow_agent":true,"use_rag":false,"max_tokens":2048,"messages":[{"role":"user","content":prompt}]}))).await;
    let definitions = request(&mut core, &vault, "GET", "/api/agent/definitions", None).await;
    assert_eq!(definitions["items"].as_array().unwrap().len(), 2);
    let groups = request(&mut core, &vault, "GET", "/api/agent/collaborations", None).await;
    assert_eq!(groups["items"].as_array().unwrap().len(), 1);
    let group = &groups["items"][0];
    assert_eq!(group["status"], "awaiting_confirmation");
    let id = group["id"].as_str().unwrap();
    request(&mut core, &vault, "POST", &format!("/api/agent/collaborations/{id}/review"), Some(json!({"expected_revision":group["revision"],"decision":"approve"}))).await;
    let deadline = Instant::now() + Duration::from_secs(180);
    let final_group = loop {
        let state = request(&mut core, &vault, "GET", &format!("/api/agent/collaborations/{id}"), None).await;
        if !matches!(state["status"].as_str(), Some("running")) { break state; }
        assert!(Instant::now() < deadline, "live collaboration timed out");
        tokio::time::sleep(Duration::from_millis(500)).await;
    };
    assert_eq!(final_group["status"], "completed", "group status {}", final_group["status"]);
    for member in final_group["members"].as_array().unwrap() {
        let run = request(&mut core, &vault, "GET", &format!("/api/agent/runs/{}", member["run_id"].as_str().unwrap()), None).await;
        assert_eq!(run["status"], "completed");
        assert!(run["tool_results"].as_array().unwrap().iter().any(|result| result["name"] == "notes.list" && result["success"] == true));
    }
    assert!(std::fs::read_dir(&root).unwrap().all(|entry| entry.unwrap().file_name().to_string_lossy().starts_with('.')), "test must not write notes");
    println!("DeepSeek acceptance passed: two chat-created definitions, reviewed dependency plan, two real tool executions; isolated vault only.");
}

#[tokio::test]
#[ignore = "Set QA_PACKAGED_CORE and QA_PACKAGED_MANIFEST to validate a freshly built bundle"]
async fn packaged_core_managed_agents_with_real_workspace_broker() {
    let executable = PathBuf::from(std::env::var("QA_PACKAGED_CORE").unwrap());
    let manifest = std::fs::read_to_string(std::env::var("QA_PACKAGED_MANIFEST").unwrap()).unwrap();
    let temp = tempfile::tempdir().unwrap();
    let root = temp.path().join("vault"); std::fs::create_dir(&root).unwrap();
    let workspace = Arc::new(Mutex::new(Workspace::open(&root).unwrap()));
    let vault = workspace.lock().unwrap().vault_id.clone();
    let handler = workspace.clone();
    let mut core = CoreSupervisor::new(executable.clone(), vec![], executable.parent().unwrap().to_path_buf(), temp.path().join("core"))
        .with_bundle_manifest(manifest).with_broker(Arc::new(move |value| workspace_broker::dispatch(&mut handler.lock().unwrap(), value)));
    let agent = request(&mut core, &vault, "POST", "/api/agent/definitions", Some(json!({"name":"Packaged manual Agent","provider_id":"mock","model":"mock-1","tools":["notes.list"]}))).await;
    assert_eq!(agent["origin"], "manual"); assert!(agent["config"]["token_budget"].is_null());
    let group = request(&mut core, &vault, "POST", "/api/agent/collaborations", Some(json!({"title":"Packaged collaboration","members":[
        {"member_id":"a","agent_id":agent["id"],"input":"/tool notes.list {}"},
        {"member_id":"b","agent_id":agent["id"],"input":"/tool notes.list {}","depends_on":["a"]}
    ]}))).await;
    let id = group["id"].as_str().unwrap();
    let path = format!("/api/agent/collaborations/{id}");
    assert_eq!(group["status"], "awaiting_confirmation");
    request(&mut core, &vault, "POST", &format!("{path}/review"), Some(json!({"decision":"approve","expected_revision":group["revision"]}))).await;
    let deadline = Instant::now() + Duration::from_secs(30);
    let finished = loop {
        let group = request(&mut core, &vault, "GET", &path, None).await;
        if group["status"] != "running" { break group; }
        assert!(Instant::now() < deadline, "packaged collaboration timed out");
        tokio::time::sleep(Duration::from_millis(200)).await;
    };
    assert_eq!(finished["status"], "completed", "{}", finished["status"]);
    for (index, member) in finished["members"].as_array().unwrap().iter().enumerate() {
        let run = request(&mut core, &vault, "GET", &format!("/api/agent/runs/{}", member["run_id"].as_str().unwrap()), None).await;
        if index == 0 {
            assert_eq!(run["tool_results"][0]["name"], "notes.list");
            assert_eq!(run["tool_results"][0]["success"], true);
        } else {
            // Mock intentionally only parses a bare /tool command. The dependent
            // member has appended upstream evidence; verify that delivery here.
            // Actual dependent tool calling is covered by the live provider test.
            assert!(run["input"].as_str().unwrap().contains("上游执行结果"));
            assert!(run["output"].as_str().is_some_and(|text| !text.is_empty()));
        }
    }
    let repeated = request(&mut core, &vault, "POST", &format!("{path}/review"), Some(json!({"decision":"approve","expected_revision":group["revision"]}))).await;
    assert_eq!(repeated["members"], finished["members"]);
    println!("Packaged Host/Core passed: manifest verification, manual unlimited default, reviewed dependent members, real workspace tool results, no duplicate launch.");
}
