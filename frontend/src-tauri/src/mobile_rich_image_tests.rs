use super::*;
use base64::{engine::general_purpose::STANDARD, Engine};

const MARKER: &str = "2a9846b3-58ba-4fa8-b154-7dbdf6ec875e";
const BEFORE: &str = "---\r\ntitle: 中文\r\n---\r\n原文 😀\r\n";
const TEMPLATE: &str = "---\r\ntitle: 中文\r\n---\r\n原文 😀 ![](opennexus-rich-image-2a9846b3-58ba-4fa8-b154-7dbdf6ec875e)\r\n";
fn png() -> Vec<u8> {
    STANDARD.decode("iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAYAAABytg0kAAAAG0lEQVR4nGN45JD0X2l5238G8/TL/78f8/gPAFY9Ckass/NvAAAAAElFTkSuQmCC").unwrap()
}

#[test]
fn rich_image_freezes_actual_candidate_and_original_bytes_with_one_recoverable_receipt() {
    let temp = tempfile::tempdir().unwrap();
    let mut engine = MobileWorkspace::open(temp.path()).unwrap();
    let draft = engine
        .drafts
        .create_with_content(&engine.workspace, "notes/中文.md", BEFORE)
        .unwrap();
    let request = json!({"kind":"prepare_rich_image","draft_id":draft.id,"edit_session":draft.edit_session,"generation":1,"template":TEMPLATE,"marker":MARKER,"content_base64":STANDARD.encode(png())});
    let mut invalid = request.clone();
    invalid["root"] = json!("/unowned");
    assert!(serde_json::from_value::<MobileRequest>(invalid).is_err());
    let prepared = engine
        .dispatch(serde_json::from_value(request.clone()).unwrap())
        .unwrap();
    let intent: ImageIntent = serde_json::from_value(prepared.clone()).unwrap();
    assert_eq!(intent.content_before, BEFORE);
    assert_eq!(
        intent.content_after,
        TEMPLATE.replace(
            &format!("![](opennexus-rich-image-{MARKER})"),
            &format!("![]({})", intent.reference)
        )
    );
    assert!(intent.reference.starts_with("../attachments/"));
    assert!(!engine.workspace.root.join(&intent.asset_path).exists());
    assert_eq!(engine.drafts.get(&draft.id).unwrap().content, BEFORE);
    assert_eq!(
        engine
            .dispatch(serde_json::from_value(request).unwrap())
            .unwrap(),
        prepared
    );
    drop(engine);
    let mut engine = MobileWorkspace::open(temp.path()).unwrap();
    assert_eq!(
        engine.drafts.observe_image(&intent.operation_id).unwrap(),
        intent
    );
    let applied = engine
        .drafts
        .apply_image(&mut engine.workspace, &intent.operation_id)
        .unwrap();
    assert_eq!(applied.state, "committed");
    assert_eq!(
        std::fs::read(engine.workspace.root.join(&intent.asset_path)).unwrap(),
        png()
    );
    assert_eq!(
        engine.drafts.get(&draft.id).unwrap().content,
        intent.content_after
    );
    assert_eq!(engine.drafts.get(&draft.id).unwrap().generation, 2);
    assert_eq!(
        engine
            .drafts
            .apply_image(&mut engine.workspace, &intent.operation_id)
            .unwrap(),
        applied
    );
}

#[test]
fn rich_image_rejects_ambiguous_templates_stale_ownership_and_canvas_without_writes() {
    let temp = tempfile::tempdir().unwrap();
    let engine = MobileWorkspace::open(temp.path()).unwrap();
    let draft = engine
        .drafts
        .create_with_content(&engine.workspace, "notes/a.md", BEFORE)
        .unwrap();
    let encoded = STANDARD.encode(png());
    for (template, marker, generation, session) in [
        (BEFORE.to_owned(), MARKER, 1, draft.edit_session.as_str()),
        (TEMPLATE.repeat(2), MARKER, 1, draft.edit_session.as_str()),
        (
            TEMPLATE.to_owned(),
            "invalid-marker",
            1,
            draft.edit_session.as_str(),
        ),
        (
            "x".repeat(1024 * 1024 + 1),
            MARKER,
            1,
            draft.edit_session.as_str(),
        ),
        (TEMPLATE.to_owned(), MARKER, 2, draft.edit_session.as_str()),
        (TEMPLATE.to_owned(), MARKER, 1, MARKER),
    ] {
        assert!(engine
            .drafts
            .prepare_rich_image(
                &engine.workspace,
                &draft.id,
                session,
                generation,
                &template,
                marker,
                &encoded
            )
            .is_err());
        assert!(engine.drafts.pending_image(&draft.id).unwrap().is_none());
        assert_eq!(engine.drafts.get(&draft.id).unwrap().content, BEFORE);
    }
    let canvas = engine
        .drafts
        .create_with_content(&engine.workspace, "map.canvas", EMPTY_CANVAS)
        .unwrap();
    assert!(engine
        .drafts
        .prepare_rich_image(
            &engine.workspace,
            &canvas.id,
            &canvas.edit_session,
            1,
            TEMPLATE,
            MARKER,
            &encoded
        )
        .is_err());
    let intent = engine
        .drafts
        .prepare_rich_image(
            &engine.workspace,
            &draft.id,
            &draft.edit_session,
            1,
            TEMPLATE,
            MARKER,
            &encoded,
        )
        .unwrap();
    assert!(engine
        .drafts
        .prepare_rich_image(
            &engine.workspace,
            &draft.id,
            &draft.edit_session,
            1,
            &TEMPLATE.replace("原文", "changed"),
            MARKER,
            &encoded
        )
        .is_err());
    assert_eq!(
        engine.drafts.observe_image(&intent.operation_id).unwrap(),
        intent
    );
    assert!(!engine.workspace.root.join(&intent.asset_path).exists());
}
