//! Serial MCP session over an already-authorized native instance. The Host
//! approval route still must establish user consent and current installation/trust.
use crate::{
    extension_io::{Event, Pump},
    extension_process::Running,
    extension_stdio::HostIo,
    workspace::{HostError, Result},
};
use serde::Deserialize;
use serde_json::{json, Value};
use std::{
    sync::atomic::{AtomicBool, Ordering},
    time::{Duration, Instant},
};
pub const PROTOCOL_VERSION: &str = "2025-11-25";
const VERSIONS: [&str; 4] = [PROTOCOL_VERSION, "2025-06-18", "2025-03-26", "2024-11-05"];
fn present<'de, D: serde::Deserializer<'de>, T: Deserialize<'de>>(
    deserializer: D,
) -> std::result::Result<Option<T>, D::Error> {
    T::deserialize(deserializer).map(Some)
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Envelope {
    jsonrpc: String,
    #[serde(default, deserialize_with = "present")]
    id: Option<Value>,
    #[serde(default, deserialize_with = "present")]
    method: Option<String>,
    #[serde(default, deserialize_with = "present")]
    params: Option<Value>,
    #[serde(default, deserialize_with = "present")]
    result: Option<Value>,
    #[serde(default, deserialize_with = "present")]
    error: Option<RemoteError>,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RemoteError {
    code: i64,
    message: String,
    data: Option<Value>,
}
fn invalid() -> HostError {
    HostError::new("EXTENSION_MCP_PROTOCOL_INVALID")
}
fn decode(bytes: &[u8]) -> Result<Envelope> {
    let value: Envelope = serde_json::from_slice(bytes).map_err(|_| invalid())?;
    if value.jsonrpc != "2.0" || value.params.as_ref().is_some_and(|v| !v.is_object()) {
        return Err(invalid());
    }
    if let Some(id) = &value.id {
        if id.as_i64().is_none() && !id.as_str().is_some_and(|s| !s.is_empty() && s.len() <= 128) {
            return Err(invalid());
        }
    }
    if let Some(method) = &value.method {
        if method.is_empty()
            || method.len() > 256
            || method.chars().any(char::is_control)
            || value.result.is_some()
            || value.error.is_some()
        {
            return Err(invalid());
        }
    } else if value.id.is_none()
        || value.params.is_some()
        || value.result.is_some() == value.error.is_some()
    {
        return Err(invalid());
    }
    if value.result.as_ref().is_some_and(|v| !v.is_object()) {
        return Err(invalid());
    }
    if let Some(error) = &value.error {
        if error.message.len() > 4096
            || error.code < i32::MIN as i64
            || error.code > i32::MAX as i64
        {
            return Err(invalid());
        }
        // Error data is never propagated or logged; it may contain secrets.
        let _ = &error.data;
    }
    Ok(value)
}
pub struct Session<'a, 'p> {
    process: &'a Running<'p>,
    pump: Pump,
    next: u64,
    ready: bool,
    tools: bool,
    failed: bool,
    tools_changed: bool,
    catalog: Option<crate::extension_mcp_tools::Catalog>,
    calls: crate::extension_call_authorization::Gate,
}
impl<'a, 'p> Session<'a, 'p> {
    pub fn new(process: &'a Running<'p>, io: HostIo) -> Result<Self> {
        Ok(Self {
            process,
            pump: process.start_io(io)?,
            next: 1,
            ready: false,
            tools: false,
            failed: false,
            tools_changed: false,
            catalog: None,
            calls: crate::extension_call_authorization::Gate::new(process.call_identity()?),
        })
    }
    pub fn initialize(&mut self, cancel: &AtomicBool) -> Result<String> {
        if self.ready {
            return Err(HostError::new("EXTENSION_MCP_ALREADY_INITIALIZED"));
        }
        let value = self.request("initialize", json!({"protocolVersion":PROTOCOL_VERSION,"capabilities":{},"clientInfo":{"name":"OpenNexus","version":env!("CARGO_PKG_VERSION")}}), Duration::from_secs(10), cancel)?;
        let version = value["protocolVersion"]
            .as_str()
            .filter(|v| VERSIONS.contains(v));
        let info = value["serverInfo"].as_object();
        let capabilities = value["capabilities"].as_object();
        if version.is_none()
            || info.is_none()
            || capabilities.is_none()
            || !value["serverInfo"]["name"]
                .as_str()
                .is_some_and(|s| !s.is_empty() && s.len() <= 256)
            || !value["serverInfo"]["version"]
                .as_str()
                .is_some_and(|s| !s.is_empty() && s.len() <= 128)
            || value["capabilities"]
                .get("tools")
                .is_some_and(|v| !v.is_object())
        {
            self.abort();
            return Err(HostError::new("EXTENSION_MCP_INITIALIZATION_INVALID"));
        }
        self.tools = value["capabilities"].get("tools").is_some();
        if let Err(error) = self.send(json!({"jsonrpc":"2.0","method":"notifications/initialized"}))
        {
            self.abort();
            return Err(error);
        }
        self.ready = true;
        Ok(version.unwrap().into())
    }
    pub fn refresh_tools(
        &mut self,
        cancel: &AtomicBool,
    ) -> Result<Vec<crate::extension_mcp_tools::Description>> {
        self.require_tools()?;
        self.drain_pending()?;
        self.catalog = None;
        self.calls.invalidate();
        self.tools_changed = false;
        let started = Instant::now();
        let catalog = crate::extension_mcp_tools::Catalog::discover(|cursor| {
            let remaining = Duration::from_secs(20).saturating_sub(started.elapsed());
            if remaining.is_zero() {
                return Err(HostError::new("EXTENSION_MCP_CATALOG_TIMEOUT"));
            }
            self.list_tools_page(cursor, cancel, remaining.min(Duration::from_secs(10)))
        });
        let catalog = match catalog {
            Ok(catalog) => catalog,
            Err(error) => {
                self.abort();
                return Err(error);
            }
        };
        self.drain_pending()?;
        if self.tools_changed {
            return Err(HostError::new("EXTENSION_MCP_CATALOG_CHANGED"));
        }
        let descriptions = catalog.descriptions();
        self.catalog = Some(catalog);
        Ok(descriptions)
    }
    fn list_tools_page(
        &mut self,
        cursor: Option<&str>,
        cancel: &AtomicBool,
        budget: Duration,
    ) -> Result<Value> {
        self.require_tools()?;
        if cursor.is_some_and(|s| s.is_empty() || s.len() > 1024) {
            return Err(invalid());
        }
        let value = self.request(
            "tools/list",
            cursor.map_or_else(|| json!({}), |c| json!({"cursor":c})),
            budget,
            cancel,
        )?;
        if !value["tools"]
            .as_array()
            .is_some_and(|tools| tools.len() <= 500)
            || value
                .get("nextCursor")
                .is_some_and(|v| !v.as_str().is_some_and(|s| !s.is_empty() && s.len() <= 1024))
        {
            self.abort();
            return Err(invalid());
        }
        Ok(value)
    }
    pub fn review_call(
        &mut self,
        name: &str,
        arguments: Value,
    ) -> Result<crate::extension_call_authorization::Review> {
        self.require_tools()?;
        self.drain_pending()?;
        let tool = self
            .catalog
            .as_ref()
            .ok_or_else(|| HostError::new("EXTENSION_MCP_CATALOG_REQUIRED"))?
            .tool(name)?;
        self.calls.review(&tool, arguments)
    }
    /// Only invoke from an authenticated Host route after the user approved this
    /// exact review. This method is not registered as a renderer/Core command.
    pub fn confirm_call(
        &mut self,
        review_id: &str,
    ) -> Result<crate::extension_call_authorization::ApprovedCall> {
        self.require_tools()?;
        self.drain_pending()?;
        self.calls.confirm(review_id)
    }
    pub fn call_tool(
        &mut self,
        approved: crate::extension_call_authorization::ApprovedCall,
        cancel: &AtomicBool,
    ) -> Result<Value> {
        self.require_tools()?;
        self.drain_pending()?;
        let call = self.calls.consume(approved)?;
        let tool = self
            .catalog
            .as_ref()
            .ok_or_else(|| HostError::new("EXTENSION_MCP_CATALOG_REQUIRED"))?
            .tool(&call.name)?;
        if crate::extension_call_authorization::contract_digest(&tool.description())?
            != call.contract_digest
        {
            return Err(HostError::new("EXTENSION_CALL_CATALOG_CHANGED"));
        }
        self.invoke_tool(&call.name, call.arguments, cancel)
    }
    #[cfg(test)]
    pub(crate) fn test_call_tool(
        &mut self,
        name: &str,
        arguments: Value,
        cancel: &AtomicBool,
    ) -> Result<Value> {
        let review = self.review_call(name, arguments)?;
        let approved = self.confirm_call(&review.review_id)?;
        self.call_tool(approved, cancel)
    }
    fn invoke_tool(&mut self, name: &str, arguments: Value, cancel: &AtomicBool) -> Result<Value> {
        self.require_tools()?;
        self.drain_pending()?;
        if name.is_empty()
            || name.len() > 256
            || name.chars().any(char::is_control)
            || !arguments.is_object()
        {
            return Err(invalid());
        }
        let tool = self
            .catalog
            .as_ref()
            .ok_or_else(|| HostError::new("EXTENSION_MCP_CATALOG_REQUIRED"))?
            .tool(name)?;
        tool.validate_arguments(&arguments)?;
        let value = self.request(
            "tools/call",
            json!({"name":name,"arguments":arguments}),
            Duration::from_secs(60),
            cancel,
        )?;
        if let Err(error) = tool.validate_result(&value) {
            self.abort();
            return Err(error);
        }
        Ok(value)
    }
    fn require_tools(&self) -> Result<()> {
        if self.failed {
            return Err(HostError::new("EXTENSION_MCP_SESSION_FAILED"));
        }
        if !self.ready {
            return Err(HostError::new("EXTENSION_MCP_NOT_INITIALIZED"));
        }
        if !self.tools {
            return Err(HostError::new("EXTENSION_MCP_TOOLS_UNAVAILABLE"));
        }
        Ok(())
    }
    fn server_message(&mut self, message: &Envelope) -> Result<bool> {
        let Some(method) = &message.method else {
            return Ok(false);
        };
        if let Some(id) = &message.id {
            self.send(if method == "ping" { json!({"jsonrpc":"2.0","id":id,"result":{}}) }
                else { json!({"jsonrpc":"2.0","id":id,"error":{"code":-32601,"message":"Method not supported"}}) })?;
        } else if method == "notifications/tools/list_changed" {
            self.tools_changed = true;
            self.catalog = None;
            self.calls.invalidate();
        }
        Ok(true)
    }
    /// Apply already-received notifications before selecting a cached contract.
    /// The eventual registry loop must also call this while the instance is idle.
    pub fn drain_pending(&mut self) -> Result<()> {
        let result = (|| {
            for _ in 0..128 {
                self.process.check_authorization()?;
                match self.pump.receive(Duration::ZERO) {
                    Err(error) if error.code == "EXTENSION_IO_TIMEOUT" => return Ok(()),
                    Err(error) => return Err(error),
                    Ok(Event::Closed) => {
                        return Err(HostError::new("EXTENSION_MCP_CONNECTION_CLOSED"))
                    }
                    Ok(Event::Frame(bytes)) => {
                        let message = decode(&bytes)?;
                        if !self.server_message(&message)? {
                            return Err(HostError::new("EXTENSION_MCP_UNEXPECTED_RESPONSE"));
                        }
                    }
                }
            }
            Err(HostError::new("EXTENSION_IO_RATE_LIMITED"))
        })();
        if result.is_err() {
            self.abort();
        }
        result
    }
    fn send(&self, value: Value) -> Result<()> {
        self.pump.send_wait(
            serde_json::to_vec(&value).map_err(|_| invalid())?,
            Duration::from_millis(50),
        )
    }
    fn abort(&mut self) {
        self.failed = true;
        self.calls.invalidate();
        let _ = self.process.terminate();
    }
    fn request(
        &mut self,
        method: &str,
        params: Value,
        budget: Duration,
        cancel: &AtomicBool,
    ) -> Result<Value> {
        if self.failed {
            return Err(HostError::new("EXTENSION_MCP_SESSION_FAILED"));
        }
        if cancel.load(Ordering::Acquire) {
            return Err(HostError::new("EXTENSION_MCP_CANCELLED"));
        }
        let id = format!("opennexus.{}", self.next);
        self.next = self.next.checked_add(1).ok_or_else(invalid)?;
        let deadline = self.process.start_tool_call()?;
        let started = Instant::now();
        let result = (|| {
            self.send(json!({"jsonrpc":"2.0","id":id,"method":method,"params":params}))?;
            loop {
                self.process.check_authorization()?;
                deadline.check()?;
                if cancel.load(Ordering::Acquire) || started.elapsed() >= budget {
                    // initialize cannot be cancelled at the protocol level.
                    if method != "initialize" {
                        let _ = self.send(json!({"jsonrpc":"2.0","method":"notifications/cancelled","params":{"requestId":id}}));
                    }
                    return Err(HostError::new(if cancel.load(Ordering::Acquire) {
                        "EXTENSION_MCP_CANCELLED"
                    } else {
                        "EXTENSION_MCP_TIMEOUT"
                    }));
                }
                let received = self.pump.receive(Duration::from_millis(20));
                self.process.check_authorization()?;
                deadline.check()?;
                let bytes = match received {
                    Ok(Event::Frame(bytes)) => bytes,
                    Ok(Event::Closed) => {
                        return Err(HostError::new("EXTENSION_MCP_CONNECTION_CLOSED"))
                    }
                    Err(error) if error.code == "EXTENSION_IO_TIMEOUT" => continue,
                    Err(error) => return Err(error),
                };
                let message = decode(&bytes)?;
                if self.server_message(&message)? {
                    continue;
                }
                if message.id != Some(json!(id)) {
                    return Err(HostError::new("EXTENSION_MCP_RESPONSE_ID_MISMATCH"));
                }
                return Ok(message.result);
            }
        })();
        match result {
            Ok(value) => {
                if let Err(error) = deadline.finish() {
                    self.abort();
                    return Err(error);
                }
                value.ok_or_else(|| HostError::new("EXTENSION_MCP_REMOTE_ERROR"))
            }
            Err(error) => {
                self.abort();
                drop(deadline);
                Err(error)
            }
        }
    }
    pub(crate) fn is_failed(&self) -> bool {
        self.failed
    }
    pub fn take_tools_changed(&mut self) -> bool {
        std::mem::take(&mut self.tools_changed)
    }
    pub fn shutdown(mut self) -> Result<()> {
        self.pump.close_input();
        let _ = self.process.wait(Duration::from_millis(200));
        self.pump.shutdown()
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn malformed_and_ambiguous_envelopes_are_rejected() {
        for input in [
            r#"[]"#,
            r#"{"jsonrpc":"2.0","id":null,"method":"ping"}"#,
            r#"{"jsonrpc":"2.0","method":"ping","params":null}"#,
            r#"{"jsonrpc":"2.0","id":1,"id":2,"result":{}}"#,
            r#"{"jsonrpc":"1.0","id":1,"result":{}}"#,
            r#"{"jsonrpc":"2.0","id":null,"result":{}}"#,
            r#"{"jsonrpc":"2.0","id":1,"result":{},"error":{"code":1,"message":"bad"}}"#,
            r#"{"jsonrpc":"2.0","id":1,"method":"ping","result":{}}"#,
        ] {
            assert!(decode(input.as_bytes()).is_err(), "{input}");
        }
        assert!(decode(br#"{"jsonrpc":"2.0","id":"request","result":{}}"#).is_ok());
        assert!(decode(br#"{"jsonrpc":"2.0","id":1,"method":"ping"}"#).is_ok());
        assert!(
            decode(br#"{"jsonrpc":"2.0","method":"notifications/tools/list_changed"}"#).is_ok()
        );
    }
}
