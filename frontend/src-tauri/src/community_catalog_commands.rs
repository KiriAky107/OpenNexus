use crate::{extension_commands::main_window, Host};
use notesagent_host::community_catalog::{self, Reply};
use serde::Deserialize;
use tauri::{State, WebviewWindow};

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Request {
    request_id: String,
    query: community_catalog::Request,
}
#[tauri::command]
pub async fn community_catalog_request(
    window: WebviewWindow,
    host: State<'_, Host>,
    request: Request,
) -> Result<Reply, String> {
    main_window(&window)?;
    let mut lease = host.extension_requests.claim(&request.request_id)?;
    lease
        .run(async {
            community_catalog::fetch(&request.query)
                .await
                .map_err(|error| error.code)
        })
        .await
}
