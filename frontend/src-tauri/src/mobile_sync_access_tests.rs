use super::*;
use crate::mobile_workspace::MobileWorkspace;

#[test]
fn sync_workspace_access_does_not_open_storage_or_hide_native_errors() {
    let handle: Arc<Mutex<Option<MobileWorkspace>>> = Arc::new(Mutex::new(None));
    let denied: Result<()> =
        handle.access(|_| panic!("Unopened mobile Vault must not be accessed"));
    assert_eq!(denied.unwrap_err().code, "MOBILE_STORAGE_UNAVAILABLE");

    let root = tempfile::tempdir().unwrap();
    *handle.lock().unwrap() = Some(MobileWorkspace::open(root.path()).unwrap());
    let rejected: Result<()> =
        handle.access(|_| Err(crate::workspace::HostError::new("OWNED_TEST_REJECTION")));
    assert_eq!(rejected.unwrap_err().code, "OWNED_TEST_REJECTION");
    assert!(handle.access(|ws| ws.tree()).unwrap().is_empty());
}

#[test]
fn sync_workspace_access_preserves_dirty_draft_when_confirmed_file_changes() {
    let root = tempfile::tempdir().unwrap();
    let mut engine = MobileWorkspace::open(root.path()).unwrap();
    let entry = engine
        .workspace
        .write("notes/中文.md", "", b"initial\r\n", "local")
        .unwrap();
    let draft = engine
        .drafts
        .open_note(&mut engine.workspace, &entry.file_id)
        .unwrap();
    let original_draft = "未确认草稿 😀\r\n";
    engine
        .drafts
        .persist(&draft.id, &draft.edit_session, 1, original_draft)
        .unwrap();
    let handle = Arc::new(Mutex::new(Some(engine)));
    let binding = handle
        .access(|ws| ws.sync_bind_empty("https://sync.example", "remote", "account"))
        .unwrap();
    let confirmed = b"concurrent confirmed content\r\n";
    handle
        .access(|ws| ws.write("notes/中文.md", &entry.hash, confirmed, "local"))
        .unwrap();
    let operation_id;
    {
        let mut guard = handle.lock().unwrap();
        let engine = guard.as_mut().unwrap();
        let intent = engine
            .drafts
            .prepare_save(&engine.workspace, &draft.id, &draft.edit_session, 1)
            .unwrap();
        operation_id = intent.operation_id;
        assert_eq!(
            engine
                .drafts
                .apply_save(&mut engine.workspace, &operation_id)
                .unwrap_err()
                .code,
            "REVISION_CONFLICT"
        );
        assert_eq!(
            engine.drafts.get(&draft.id).unwrap().content,
            original_draft
        );
    }
    let document = handle.access(|ws| ws.read("notes/中文.md")).unwrap();
    assert_eq!(document.content.as_bytes(), confirmed);
    assert_eq!(document.entry.file_id, entry.file_id);
    assert!(handle
        .access(|ws| ws.sync_next(&binding.id))
        .unwrap()
        .is_some());
    drop(handle);
    let mut reopened = MobileWorkspace::open(root.path()).unwrap();
    assert_eq!(
        reopened
            .workspace
            .read("notes/中文.md")
            .unwrap()
            .content
            .as_bytes(),
        confirmed
    );
    let retained = reopened.drafts.get(&draft.id).unwrap();
    assert_eq!(retained.content, original_draft);
    assert!(retained.generation > retained.saved_generation);
    assert_eq!(
        reopened.drafts.intent(&operation_id).unwrap().state,
        "rejected"
    );
}
