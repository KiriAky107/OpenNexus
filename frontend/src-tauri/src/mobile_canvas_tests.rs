use super::*;
use crate::mobile_drafts::MAX_NOTE_BYTES;
use crate::workspace::hash;

const CANVAS: &str = "{\r\n  \"nodes\": [{\"id\":\"甲\",\"type\":\"text\",\"x\":-30,\"y\":40,\"width\":300,\"height\":180,\"text\":\"中文 😀\",\"extra\":{\"keep\":1}}],\r\n  \"edges\":[],\"extension\":{\"original\":true}\r\n}\r\n";

#[test]
fn native_canvas_create_list_read_and_frozen_save_keep_original_json_bytes() {
    let temp = tempfile::tempdir().unwrap();
    let mut engine = MobileWorkspace::open(temp.path()).unwrap();
    let created = engine
        .dispatch(MobileRequest::CreateDraft {
            path: "画布/自由.canvas".into(),
            content: Some(CANVAS.into()),
        })
        .unwrap();
    let draft: Draft = serde_json::from_value(created["draft"].clone()).unwrap();
    let intent = engine
        .drafts
        .prepare_save(
            &engine.workspace,
            &draft.id,
            &draft.edit_session,
            draft.generation,
        )
        .unwrap();
    assert_eq!(intent.content, CANVAS);
    let saved = engine
        .drafts
        .apply_save(&mut engine.workspace, &intent.operation_id)
        .unwrap();
    assert_eq!(saved.state, "committed");
    let file_id = engine.drafts.get(&draft.id).unwrap().file_id.unwrap();
    let read = engine
        .dispatch(MobileRequest::ReadNote {
            file_id: file_id.clone(),
        })
        .unwrap();
    assert_eq!(read["content"], CANVAS);
    assert_eq!(read["hash"], hash(CANVAS.as_bytes()));
    let listed = engine
        .dispatch(MobileRequest::ListNotes {
            offset: 0,
            limit: 20,
            query: "中文".into(),
            directory: "".into(),
        })
        .unwrap();
    assert_eq!(listed["total"], 1);
    assert_eq!(listed["items"][0]["file_id"], file_id);
    assert_eq!(
        engine
            .drafts
            .observe_save(&engine.workspace, &intent.operation_id)
            .unwrap(),
        saved
    );
    assert_eq!(
        std::fs::read(engine.workspace.root.join("画布/自由.canvas")).unwrap(),
        CANVAS.as_bytes()
    );
}

#[test]
fn canvas_capacity_is_separate_and_invalid_json_is_recoverable_but_never_saved() {
    let temp = tempfile::tempdir().unwrap();
    let mut engine = MobileWorkspace::open(temp.path()).unwrap();
    let source = serde_json::json!({"nodes":[],"edges":[],"unknown":"x".repeat(MAX_NOTE_BYTES+10)})
        .to_string();
    let draft = engine
        .drafts
        .create_with_content(&engine.workspace, "large.canvas", &source)
        .unwrap();
    let intent = engine
        .drafts
        .prepare_save(&engine.workspace, &draft.id, &draft.edit_session, 1)
        .unwrap();
    engine
        .drafts
        .apply_save(&mut engine.workspace, &intent.operation_id)
        .unwrap();
    let current = engine.drafts.get(&draft.id).unwrap();
    let invalid = "{ unfinished original input";
    engine
        .drafts
        .persist(&draft.id, &draft.edit_session, 2, invalid)
        .unwrap();
    assert_eq!(
        engine
            .drafts
            .prepare_save(&engine.workspace, &draft.id, &draft.edit_session, 2)
            .unwrap_err()
            .code,
        "CANVAS_INVALID"
    );
    assert_eq!(engine.drafts.get(&draft.id).unwrap().content, invalid);
    assert!(engine.drafts.pending(&draft.id).unwrap().is_none());
    assert_eq!(
        engine.workspace.read("large.canvas").unwrap().content,
        source
    );
    assert_eq!(current.saved_generation, 1);
    assert_eq!(
        engine
            .drafts
            .persist(
                &draft.id,
                &draft.edit_session,
                3,
                &"x".repeat(crate::canvas_contract::MAX_CANVAS_BYTES + 1)
            )
            .unwrap_err()
            .code,
        "MOBILE_DRAFT_INVALID"
    );
    assert!(engine
        .drafts
        .create_with_content(&engine.workspace, "still.md", &source)
        .is_err());
    drop(engine);
    let engine = MobileWorkspace::open(temp.path()).unwrap();
    assert_eq!(engine.drafts.get(&draft.id).unwrap().content, invalid);
}

#[test]
fn canvas_rename_delete_keep_identity_and_do_not_silently_convert_format() {
    let temp = tempfile::tempdir().unwrap();
    let mut engine = MobileWorkspace::open(temp.path()).unwrap();
    let entry = engine
        .workspace
        .write("a.canvas", "", CANVAS.as_bytes(), "local")
        .unwrap();
    let draft = engine
        .drafts
        .open_note(&mut engine.workspace, &entry.file_id)
        .unwrap();
    assert_eq!(
        engine
            .drafts
            .copy_as(
                &engine.workspace,
                &draft.id,
                &draft.edit_session,
                "wrong.md"
            )
            .unwrap_err()
            .code,
        "MOBILE_DOCUMENT_TYPE_CHANGED"
    );
    assert_eq!(
        engine
            .drafts
            .prepare_mutation(
                &mut engine.workspace,
                "rename",
                &entry.file_id,
                &entry.hash,
                "wrong.md"
            )
            .unwrap_err()
            .code,
        "MOBILE_DOCUMENT_TYPE_CHANGED"
    );
    let rename = engine
        .drafts
        .prepare_mutation(
            &mut engine.workspace,
            "rename",
            &entry.file_id,
            &entry.hash,
            "画布/已改名.canvas",
        )
        .unwrap();
    engine
        .drafts
        .apply_mutation(&mut engine.workspace, &rename.operation_id)
        .unwrap();
    let read = engine.read(&entry.file_id).unwrap();
    assert_eq!(read.entry.path, "画布/已改名.canvas");
    assert_eq!(read.content, CANVAS);
    let delete = engine
        .drafts
        .prepare_mutation(
            &mut engine.workspace,
            "delete",
            &entry.file_id,
            &entry.hash,
            "",
        )
        .unwrap();
    engine
        .drafts
        .apply_mutation(&mut engine.workspace, &delete.operation_id)
        .unwrap();
    assert_eq!(
        engine
            .drafts
            .observe_mutation(&engine.workspace, &delete.operation_id)
            .unwrap()
            .state,
        "committed"
    );
    assert!(!engine.workspace.root.join("画布/已改名.canvas").exists());
}

#[test]
fn native_canvas_default_is_valid_and_markdown_image_insertion_cannot_corrupt_it() {
    use base64::{engine::general_purpose::STANDARD, Engine};
    let temp = tempfile::tempdir().unwrap();
    let mut engine = MobileWorkspace::open(temp.path()).unwrap();
    let created = engine
        .dispatch(MobileRequest::CreateDraft {
            path: "new.canvas".into(),
            content: None,
        })
        .unwrap();
    let draft: Draft = serde_json::from_value(created["draft"].clone()).unwrap();
    crate::canvas_contract::validate(draft.content.as_bytes()).unwrap();
    assert_eq!(
        engine
            .drafts
            .prepare_image(
                &engine.workspace,
                &draft.id,
                &draft.edit_session,
                1,
                0,
                0,
                &STANDARD.encode(b"\x89PNG\r\n\x1a\noriginal")
            )
            .unwrap_err()
            .code,
        "MOBILE_IMAGE_TARGET_UNSUPPORTED"
    );
    assert!(engine.drafts.pending_image(&draft.id).unwrap().is_none());
    assert_eq!(engine.drafts.get(&draft.id).unwrap(), draft);
}
