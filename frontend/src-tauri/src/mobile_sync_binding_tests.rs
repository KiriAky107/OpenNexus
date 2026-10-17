use super::*;
use crate::sync_initial::Snapshot;

fn setup() -> (tempfile::TempDir, Workspace, Request, Snapshot) {
    let root = tempfile::tempdir().unwrap();
    let mut ws = Workspace::open(root.path()).unwrap();
    ws.write("original.md", "", b"original\r\n", "local")
        .unwrap();
    let selection = Selection {
        revision: 1,
        endpoint: "https://sync.example/".into(),
        account: "owned-a".into(),
        operation_id: Uuid::new_v4().to_string(),
        generation: 1,
    };
    let snapshot = Snapshot {
        boundary: 0,
        items: vec![],
    };
    let preview = ws
        .sync_preview(
            &selection.endpoint,
            "remote-a",
            &selection.account,
            &snapshot,
        )
        .unwrap();
    let request = Request {
        operation_id: Uuid::new_v4().to_string(),
        local_vault: ws.vault_id.clone(),
        remote_vault: "remote-a".into(),
        selection,
        preview_fingerprint: preview.fingerprint,
    };
    (root, ws, request, snapshot)
}
fn scope(request: &Request) -> Scope {
    Scope::new(&request.selection.endpoint, &request.selection.account).unwrap()
}
fn commit(
    ws: &mut Workspace,
    request: &Request,
    snapshot: &Snapshot,
    ticket: Ticket,
) -> Result<Binding> {
    ws.sync_prepare_initial(
        &request.selection.endpoint,
        &request.remote_vault,
        &request.selection.account,
        snapshot,
        &request.preview_fingerprint,
        &|| Ok(()),
    )?
    .commit_with(move |tx, binding| ticket.record(tx, binding))
}

#[test]
fn sync_legacy_binding_reads_do_not_create_metadata_or_mutate_schema() {
    let (_root, ws, request, _) = setup();
    let version: i64 = ws
        .db
        .query_row("PRAGMA schema_version", [], |row| row.get(0))
        .unwrap();
    let changes = ws.db.total_changes();
    assert!(ws
        .mobile_binding_read(&scope(&request), &request.operation_id)
        .unwrap()
        .is_none());
    let page = ws
        .mobile_binding_pending(&scope(&request), None, 20)
        .unwrap();
    assert!(page.items.is_empty());
    assert!(page.next_after.is_none());
    assert!(!exists(&ws.db).unwrap());
    assert_eq!(ws.db.total_changes(), changes);
    assert_eq!(
        ws.db
            .query_row("PRAGMA schema_version", [], |row| row.get::<_, i64>(0))
            .unwrap(),
        version
    );
}

#[test]
fn sync_original_binding_intent_survives_restart_and_never_reissues_a_ticket_on_read_or_repeat() {
    let (root, ws, request, _) = setup();
    let first = ws.mobile_binding_begin(&request).unwrap();
    assert!(first.ticket.is_some());
    assert_eq!(first.intent.attempt_revision, 1);
    drop(first);
    drop(ws);
    let ws = Workspace::open(root.path()).unwrap();
    let before = ws.db.total_changes();
    assert_eq!(
        ws.mobile_binding_read(&scope(&request), &request.operation_id)
            .unwrap()
            .unwrap()
            .request
            .operation_id,
        request.operation_id
    );
    assert!(ws.mobile_binding_begin(&request).unwrap().ticket.is_none());
    assert_eq!(ws.db.total_changes(), before);
    let mut changed = request.clone();
    changed.remote_vault = "different".into();
    assert_eq!(
        ws.mobile_binding_begin(&changed).err().unwrap().code,
        "SYNC_OPERATION_MISMATCH"
    );
    assert!(ws.sync_binding().unwrap().is_none());
    assert_eq!(
        std::fs::read(root.path().join("original.md")).unwrap(),
        b"original\r\n"
    );
}

#[test]
fn sync_explicit_resume_invalidates_old_binding_ticket_and_rolls_back_every_public_effect() {
    let (_root, mut ws, request, snapshot) = setup();
    let original = ws.mobile_binding_begin(&request).unwrap().ticket.unwrap();
    let pending_outbox: i64 = ws
        .db
        .query_row(
            "SELECT COUNT(*) FROM outbox WHERE state='pending'",
            [],
            |row| row.get(0),
        )
        .unwrap();
    let resumed = ws.mobile_binding_resume(&request, 1).unwrap();
    assert_eq!(resumed.intent.attempt_revision, 2);
    let replacement = resumed.ticket.unwrap();
    assert!(ws
        .mobile_binding_resume(&request, 1)
        .unwrap()
        .ticket
        .is_none());
    assert_eq!(
        commit(&mut ws, &request, &snapshot, original)
            .err()
            .unwrap()
            .code,
        "SYNC_BINDING_INTENT_CHANGED"
    );
    assert!(ws.sync_binding().unwrap().is_none());
    assert_eq!(
        ws.db
            .query_row(
                "SELECT COUNT(*) FROM outbox WHERE state='pending'",
                [],
                |row| row.get::<_, i64>(0)
            )
            .unwrap(),
        pending_outbox
    );
    assert_eq!(
        ws.mobile_binding_read(&scope(&request), &request.operation_id)
            .unwrap()
            .unwrap()
            .attempt_revision,
        2
    );
    let binding = commit(&mut ws, &request, &snapshot, replacement).unwrap();
    let receipt = ws
        .mobile_binding_read(&scope(&request), &request.operation_id)
        .unwrap()
        .unwrap();
    assert!(receipt.phase == Phase::Bound);
    assert_eq!(receipt.binding.unwrap().id, binding.id);
    assert!(ws.mobile_binding_begin(&request).unwrap().ticket.is_none());
    assert!(ws
        .mobile_binding_resume(&request, 2)
        .unwrap()
        .ticket
        .is_none());
    assert!(ws
        .mobile_binding_pending(&scope(&request), None, 20)
        .unwrap()
        .items
        .is_empty());
}

#[test]
fn sync_pending_binding_pages_are_scoped_bounded_and_never_rearm_operations() {
    let (_root, ws, mut request, _) = setup();
    for number in 1..=4 {
        request.operation_id = format!("00000000-0000-4000-8000-{number:012}");
        if number == 4 {
            request.selection.account = "other-account".into();
        }
        ws.mobile_binding_begin(&request).unwrap();
    }
    let own = Scope::new(&request.selection.endpoint, "owned-a").unwrap();
    let before = ws.db.total_changes();
    let first = ws.mobile_binding_pending(&own, None, 2).unwrap();
    assert_eq!(first.items.len(), 2);
    let second = ws
        .mobile_binding_pending(&own, first.next_after.as_deref(), 2)
        .unwrap();
    assert_eq!(second.items.len(), 1);
    assert!(second.next_after.is_none());
    assert!(ws
        .mobile_binding_read(&own, &request.operation_id)
        .unwrap()
        .is_none());
    assert_eq!(ws.db.total_changes(), before);
    assert_eq!(
        ws.mobile_binding_pending(&own, None, 51)
            .err()
            .unwrap()
            .code,
        "SYNC_BINDING_PAGE_INVALID"
    );
}

#[test]
fn sync_binding_request_and_corrupt_receipt_fail_without_replacing_the_original() {
    let (_root, ws, request, _) = setup();
    for field in [
        "local_vault",
        "remote_vault",
        "preview_fingerprint",
        "operation_id",
    ] {
        let mut value = serde_json::to_value(&request).unwrap();
        value[field] = serde_json::json!("invalid/metadata");
        let invalid_request: Request = serde_json::from_value(value).unwrap();
        assert!(ws.mobile_binding_begin(&invalid_request).is_err());
    }
    assert!(!exists(&ws.db).unwrap());
    ws.mobile_binding_begin(&request).unwrap();
    ws.db
        .execute(
            "UPDATE mobile_sync_binding_intents SET request_hash=?1",
            ["0".repeat(64)],
        )
        .unwrap();
    assert_eq!(
        ws.mobile_binding_read(&scope(&request), &request.operation_id)
            .err()
            .unwrap()
            .code,
        "SYNC_BINDING_INTENT_INVALID"
    );
    assert!(ws.sync_binding().unwrap().is_none());
}
