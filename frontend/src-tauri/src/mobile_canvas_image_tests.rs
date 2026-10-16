use super::*;
use base64::{engine::general_purpose::STANDARD, Engine};

const BEFORE: &str = "{\r\n\"nodes\":[],\"edges\":[],\"vendor\":{\"original\":\"中文 😀\"}}\r\n";
const PNG: &[u8] = b"\x89PNG\r\n\x1a\noriginal-owned-image-bytes";

#[test]
fn canvas_image_freezes_root_relative_reference_and_recovers_same_node_after_reopen() {
    let temp = tempfile::tempdir().unwrap();
    let engine = MobileWorkspace::open(temp.path()).unwrap();
    let draft = engine
        .drafts
        .create_with_content(&engine.workspace, "画布/自由.canvas", BEFORE)
        .unwrap();
    let intent = engine
        .drafts
        .prepare_canvas_image(
            &engine.workspace,
            &draft.id,
            &draft.edit_session,
            1,
            -80,
            120,
            &STANDARD.encode(PNG),
        )
        .unwrap();
    assert_eq!(intent.reference, intent.asset_path);
    assert!(!intent.reference.starts_with("../"));
    assert_eq!(intent.content_before, BEFORE);
    assert!(!engine.workspace.root.join(&intent.asset_path).exists());
    assert_eq!(
        engine
            .drafts
            .prepare_canvas_image(
                &engine.workspace,
                &draft.id,
                &draft.edit_session,
                1,
                -80,
                120,
                &STANDARD.encode(PNG)
            )
            .unwrap(),
        intent
    );
    assert_eq!(
        engine
            .drafts
            .observe_image(&intent.operation_id)
            .unwrap()
            .state,
        "prepared"
    );
    drop(engine);
    let mut engine = MobileWorkspace::open(temp.path()).unwrap();
    engine.drafts.checkout(&draft.id).unwrap();
    let committed = engine
        .drafts
        .apply_image(&mut engine.workspace, &intent.operation_id)
        .unwrap();
    assert_eq!(committed.state, "committed");
    assert_eq!(
        std::fs::read(engine.workspace.root.join(&intent.asset_path)).unwrap(),
        PNG
    );
    let actual = engine.drafts.get(&draft.id).unwrap();
    assert_eq!(actual.generation, 2);
    assert_eq!(actual.content, intent.content_after);
    let document: Value = serde_json::from_str(&actual.content).unwrap();
    assert_eq!(document["vendor"]["original"], "中文 😀");
    assert_eq!(document["nodes"].as_array().unwrap().len(), 1);
    assert_eq!(document["nodes"][0]["x"], -80);
    assert_eq!(document["nodes"][0]["file"], intent.asset_path);
    assert_eq!(
        engine
            .drafts
            .apply_image(&mut engine.workspace, &intent.operation_id)
            .unwrap(),
        committed
    );
    assert_eq!(engine.workspace.pending_count().unwrap(), 1);
    assert!(!engine.workspace.root.join("画布/自由.canvas").exists());
}

#[test]
fn a_canvas_image_cannot_replace_later_node_edits_or_create_an_asset_after_rejection() {
    let temp = tempfile::tempdir().unwrap();
    let mut engine = MobileWorkspace::open(temp.path()).unwrap();
    let draft = engine
        .drafts
        .create_with_content(&engine.workspace, "a.canvas", BEFORE)
        .unwrap();
    let intent = engine
        .drafts
        .prepare_canvas_image(
            &engine.workspace,
            &draft.id,
            &draft.edit_session,
            1,
            0,
            0,
            &STANDARD.encode(PNG),
        )
        .unwrap();
    let later = "{\"nodes\":[],\"edges\":[],\"later\":true}";
    engine
        .drafts
        .persist(&draft.id, &draft.edit_session, 2, later)
        .unwrap();
    assert_eq!(
        engine
            .drafts
            .apply_image(&mut engine.workspace, &intent.operation_id)
            .unwrap_err()
            .code,
        "DRAFT_GENERATION_CONFLICT"
    );
    assert_eq!(engine.drafts.get(&draft.id).unwrap().content, later);
    assert_eq!(
        engine
            .drafts
            .observe_image(&intent.operation_id)
            .unwrap()
            .state,
        "prepared"
    );
    assert!(!engine.workspace.root.join(&intent.asset_path).exists());
    assert_eq!(engine.workspace.pending_count().unwrap(), 0);
}

#[test]
fn canvas_image_api_denies_arbitrary_roots_wrong_document_types_and_out_of_range_nodes() {
    let temp = tempfile::tempdir().unwrap();
    let mut engine = MobileWorkspace::open(temp.path()).unwrap();
    let draft = engine
        .drafts
        .create_with_content(&engine.workspace, "a.canvas", BEFORE)
        .unwrap();
    let body = serde_json::json!({"kind":"prepare_canvas_image","draft_id":draft.id,"edit_session":draft.edit_session,"generation":1,"x":0,"y":0,"content_base64":STANDARD.encode(PNG)});
    let mut denied = body.clone();
    denied["root"] = serde_json::json!("/arbitrary");
    assert!(serde_json::from_value::<MobileRequest>(denied).is_err());
    let mut denied = body.clone();
    denied["x"] = serde_json::json!(1_000_001);
    assert_eq!(
        engine
            .dispatch(serde_json::from_value(denied).unwrap())
            .unwrap_err()
            .code,
        "CANVAS_INVALID"
    );
    assert!(engine.drafts.pending_image(&draft.id).unwrap().is_none());
    let md = engine.drafts.create(&engine.workspace, "note.md").unwrap();
    assert_eq!(
        engine
            .drafts
            .prepare_canvas_image(
                &engine.workspace,
                &md.id,
                &md.edit_session,
                1,
                0,
                0,
                &STANDARD.encode(PNG)
            )
            .unwrap_err()
            .code,
        "MOBILE_IMAGE_TARGET_UNSUPPORTED"
    );
    assert_eq!(engine.workspace.pending_count().unwrap(), 0);
    assert_eq!(engine.drafts.get(&draft.id).unwrap().content, BEFORE);
}
