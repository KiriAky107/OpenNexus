//! Data-only previews of verified retained bytes. Never render output as HTML.
use crate::{
    experiment_input::{SelectedFile, MAX_FILE_BYTES},
    experiment_outputs::{OutputKind, OutputManifest},
    workspace::{hash, HostError, Result, Workspace},
};
use base64::{engine::general_purpose::STANDARD, Engine};
use rusqlite::{params, OptionalExtension};
use serde::Serialize;

const MAX_TEXT: usize = 64 * 1024;
const MAX_LINES: usize = 200;
const MAX_COLUMNS: usize = 64;
const MAX_CELL: usize = 2048;
const MAX_IMAGE_SIDE: u32 = 512;

fn corrupt() -> HostError {
    HostError::new("EXPERIMENT_RECORD_CORRUPT")
}
fn range() -> HostError {
    HostError::new("EXPERIMENT_INPUT_RANGE_INVALID")
}
#[derive(Debug, Serialize)]
pub struct TextPreview {
    pub text: String,
    pub truncated: bool,
    pub lines_shown: usize,
}
#[derive(Debug, Serialize)]
#[serde(tag = "format", rename_all = "snake_case")]
pub enum PreviewContent {
    Text {
        preview: TextPreview,
    },
    Csv {
        rows: Vec<Vec<String>>,
        truncated: bool,
    },
    Png {
        width: u32,
        height: u32,
        original_width: u32,
        original_height: u32,
        content_base64: String,
    },
}
#[derive(Debug, Serialize)]
pub struct OutputPreview {
    pub manifest: OutputManifest,
    pub content: PreviewContent,
}
#[derive(Debug, Serialize)]
pub struct SourcePreview {
    pub source: SelectedFile,
    pub bytes: usize,
    pub current_path: Option<String>,
    pub preview: TextPreview,
}
#[derive(Debug, Serialize)]
pub struct SourceChunk {
    pub source: SelectedFile,
    pub bytes: usize,
    pub offset: usize,
    pub next_offset: Option<usize>,
    pub content_base64: String,
}

pub(crate) fn text_preview(text: &str) -> TextPreview {
    let mut end = text.len().min(MAX_TEXT);
    while !text.is_char_boundary(end) {
        end -= 1;
    }
    let mut lines = usize::from(!text.is_empty());
    let mut cr = false;
    for (offset, ch) in text[..end].char_indices() {
        if ch == '\r' || (ch == '\n' && !cr) {
            if lines == MAX_LINES {
                end = offset;
                break;
            }
            lines += 1;
        }
        cr = ch == '\r';
    }
    TextPreview {
        text: text[..end].into(),
        truncated: end != text.len(),
        lines_shown: lines,
    }
}

// Only called after the entire CSV has passed the shared validator. Parsing
// stops at a bounded display prefix; omitted columns/cells/rows are explicit.
fn csv_preview(text: &str) -> PreviewContent {
    let text = text.strip_prefix('\u{feff}').unwrap_or(text);
    let mut chars = text.chars().peekable();
    let mut rows = Vec::new();
    let mut row = Vec::new();
    let mut cell = String::new();
    let mut cell_truncated = false;
    let mut quoted = false;
    let mut started = false;
    let mut truncated = false;
    let mut bytes = 0usize;
    while let Some(ch) = chars.next() {
        if quoted {
            if ch == '"' {
                if chars.peek() == Some(&'"') {
                    chars.next();
                } else {
                    quoted = false;
                    continue;
                }
            }
        } else {
            match ch {
                '"' => {
                    quoted = true;
                    started = true;
                    continue;
                }
                ',' | '\r' | '\n' => {
                    if row.len() < MAX_COLUMNS {
                        row.push(std::mem::take(&mut cell));
                    } else {
                        cell.clear();
                        truncated = true;
                    }
                    cell_truncated = false;
                    if ch == ',' {
                        started = true;
                        continue;
                    }
                    if ch == '\r' && chars.peek() == Some(&'\n') {
                        chars.next();
                    }
                    rows.push(std::mem::take(&mut row));
                    started = false;
                    if rows.len() == MAX_LINES {
                        truncated |= chars.peek().is_some();
                        break;
                    }
                    continue;
                }
                _ => {}
            }
        }
        started = true;
        // Field quotes are decoded above. Embedded CRLF stays inside its cell.
        if row.len() >= MAX_COLUMNS || cell_truncated {
            truncated = true;
            continue;
        }
        if cell.len() + ch.len_utf8() > MAX_CELL {
            cell_truncated = true;
            truncated = true;
            continue;
        }
        if bytes + ch.len_utf8() > MAX_TEXT {
            truncated = true;
            break;
        }
        bytes += ch.len_utf8();
        cell.push(ch);
    }
    if started && rows.len() < MAX_LINES {
        if row.len() < MAX_COLUMNS {
            row.push(cell);
        } else {
            truncated = true;
        }
        rows.push(row);
    }
    PreviewContent::Csv { rows, truncated }
}

// Re-encode a bounded thumbnail with no source ancillary chunks. The validated
// original and its manifest stay unchanged for full-file reads and importing.
fn png_preview(bytes: &[u8]) -> Result<PreviewContent> {
    let mut decoder = png::Decoder::new(std::io::Cursor::new(bytes));
    decoder.set_limits(png::Limits {
        bytes: 32 * 1024 * 1024,
    });
    decoder.set_transformations(png::Transformations::EXPAND | png::Transformations::STRIP_16);
    let mut reader = decoder.read_info().map_err(|_| corrupt())?;
    let size = reader
        .output_buffer_size()
        .filter(|size| *size <= 32 * 1024 * 1024)
        .ok_or_else(corrupt)?;
    let mut decoded = vec![0; size];
    let info = reader.next_frame(&mut decoded).map_err(|_| corrupt())?;
    reader.finish().map_err(|_| corrupt())?;
    let channels = match info.color_type {
        png::ColorType::Grayscale => 1,
        png::ColorType::Rgb => 3,
        png::ColorType::GrayscaleAlpha => 2,
        png::ColorType::Rgba => 4,
        png::ColorType::Indexed => return Err(corrupt()),
    };
    if info.bit_depth != png::BitDepth::Eight || info.width == 0 || info.height == 0 {
        return Err(corrupt());
    }
    let side = info.width.max(info.height);
    let width = if side > MAX_IMAGE_SIDE {
        (info.width * MAX_IMAGE_SIDE / side).max(1)
    } else {
        info.width
    };
    let height = if side > MAX_IMAGE_SIDE {
        (info.height * MAX_IMAGE_SIDE / side).max(1)
    } else {
        info.height
    };
    let mut thumbnail = vec![0; width as usize * height as usize * channels];
    for y in 0..height as usize {
        let sy = y * info.height as usize / height as usize;
        for x in 0..width as usize {
            let sx = x * info.width as usize / width as usize;
            let from = (sy * info.width as usize + sx) * channels;
            let to = (y * width as usize + x) * channels;
            thumbnail[to..to + channels]
                .copy_from_slice(decoded.get(from..from + channels).ok_or_else(corrupt)?);
        }
    }
    let mut encoded = Vec::new();
    {
        let mut encoder = png::Encoder::new(&mut encoded, width, height);
        encoder.set_color(info.color_type);
        encoder.set_depth(png::BitDepth::Eight);
        let mut writer = encoder.write_header().map_err(|_| corrupt())?;
        writer.write_image_data(&thumbnail).map_err(|_| corrupt())?;
        writer.finish().map_err(|_| corrupt())?;
    }
    if encoded.len() > 2 * 1024 * 1024 {
        return Err(corrupt());
    }
    Ok(PreviewContent::Png {
        width,
        height,
        original_width: info.width,
        original_height: info.height,
        content_base64: STANDARD.encode(encoded),
    })
}

impl Workspace {
    fn experiment_source_bytes(
        &self,
        operation: &str,
        path: &str,
    ) -> Result<(SelectedFile, Vec<u8>)> {
        let record = self
            .experiment_record(operation)?
            .ok_or_else(|| HostError::new("EXPERIMENT_INPUT_NOT_FOUND"))?;
        let selected = std::iter::once(&record.summary.request.entry)
            .chain(&record.summary.request.inputs)
            .find(|source| source.path == path)
            .ok_or_else(|| HostError::new("EXPERIMENT_INPUT_NOT_FOUND"))?
            .clone();
        let size = *record.summary.sizes.get(path).ok_or_else(corrupt)?;
        let bytes: Option<Vec<u8>> = self.db.query_row(
            "SELECT content FROM experiment_inputs WHERE operation_id=?1 AND path=?2 AND length(content)=?3 AND length(content)<=?4",
            params![operation, path, size, MAX_FILE_BYTES], |r| r.get(0),
        ).optional()?;
        let bytes = bytes.ok_or_else(corrupt)?;
        if hash(&bytes) != selected.hash || std::str::from_utf8(&bytes).is_err() {
            return Err(corrupt());
        }
        Ok((selected, bytes))
    }
    pub fn experiment_source_preview(&self, operation: &str, path: &str) -> Result<SourcePreview> {
        let (source, bytes) = self.experiment_source_bytes(operation, path)?;
        let current_path = match self.path_for_id(&source.file_id) {
            Ok(path) => Some(path),
            Err(error) if error.code == "FILE_NOT_FOUND" => None,
            Err(error) => return Err(error),
        };
        Ok(SourcePreview {
            source,
            bytes: bytes.len(),
            current_path,
            preview: text_preview(std::str::from_utf8(&bytes).map_err(|_| corrupt())?),
        })
    }
    /// Raw byte offsets permit lossless streaming TextDecoder use even when a
    /// chunk splits a UTF-8 character. Current source files are never reopened.
    pub fn experiment_source_read(
        &self,
        operation: &str,
        path: &str,
        offset: usize,
        limit: usize,
    ) -> Result<SourceChunk> {
        if !(1..=256 * 1024).contains(&limit) {
            return Err(range());
        }
        let (source, bytes) = self.experiment_source_bytes(operation, path)?;
        if offset > bytes.len() {
            return Err(range());
        }
        let end = offset.saturating_add(limit).min(bytes.len());
        Ok(SourceChunk {
            source,
            bytes: bytes.len(),
            offset,
            next_offset: (end < bytes.len()).then_some(end),
            content_base64: STANDARD.encode(&bytes[offset..end]),
        })
    }
    pub fn experiment_output_preview(&self, operation: &str, path: &str) -> Result<OutputPreview> {
        let output = self.experiment_output(operation, path)?;
        let content = match output.manifest().kind {
            OutputKind::Png => png_preview(output.content())?,
            OutputKind::Csv => {
                csv_preview(std::str::from_utf8(output.content()).map_err(|_| corrupt())?)
            }
            _ => PreviewContent::Text {
                preview: text_preview(
                    std::str::from_utf8(output.content()).map_err(|_| corrupt())?,
                ),
            },
        };
        Ok(OutputPreview {
            manifest: output.manifest().clone(),
            content,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{
        experiment_input::{PreparedInputs, RunRequest},
        experiment_log::CaptureSnapshot,
        experiment_outputs::CollectedOutputs,
        experiment_policy::ExecutionLimits,
        experiment_runtime::RUNTIME_ID,
        experiment_store::{Outcome, RunResult},
    };
    fn setup(outputs: CollectedOutputs) -> (tempfile::TempDir, Workspace, RunRequest) {
        let root = tempfile::tempdir().unwrap();
        std::fs::create_dir(root.path().join("experiments")).unwrap();
        std::fs::write(
            root.path().join("experiments/中文 #%.py"),
            "# 中文\r\nprint('x')\r\n",
        )
        .unwrap();
        let mut ws = Workspace::open(root.path()).unwrap();
        let entry = ws.read("experiments/中文 #%.py").unwrap().entry;
        let request = RunRequest {
            vault_id: ws.vault_id.clone(),
            operation_id: uuid::Uuid::new_v4().to_string(),
            runtime_id: RUNTIME_ID.into(),
            entry: SelectedFile {
                file_id: entry.file_id,
                path: entry.path,
                hash: entry.hash,
                revision: entry.revision,
            },
            inputs: vec![],
            limits: ExecutionLimits::default(),
        };
        let prepared = PreparedInputs::prepare(&mut ws, &request.validate().unwrap()).unwrap();
        let record = ws.experiment_prepare(prepared).unwrap();
        ws.experiment_approve(&request.operation_id, &record.summary.fingerprint)
            .unwrap();
        let run = ws.experiment_claim(&request.operation_id).unwrap();
        ws.experiment_running(&run).unwrap();
        let mut stream = crate::experiment_log::LogBuffer::new(8192).snapshot();
        stream.complete = true;
        ws.experiment_finish_outputs(
            &run,
            RunResult {
                outcome: Outcome::Completed,
                exit_code: Some(0),
                elapsed_ms: 1,
                error: None,
                user_cpu_ticks: Some(10),
                peak_memory_bytes: Some(1000),
                final_disk_bytes: Some(outputs.total_bytes),
                logs: CaptureSnapshot {
                    stdout: stream.clone(),
                    stderr: stream,
                },
                outputs: None,
            },
            Some(&outputs),
        )
        .unwrap();
        (root, ws, request)
    }
    fn png_bytes(width: u32, height: u32, depth: png::BitDepth) -> Vec<u8> {
        let mut bytes = Vec::new();
        {
            let mut encoder = png::Encoder::new(&mut bytes, width, height);
            encoder.set_color(png::ColorType::Rgba);
            encoder.set_depth(depth);
            let mut writer = encoder.write_header().unwrap();
            let sample = if depth == png::BitDepth::Sixteen {
                vec![255, 255, 0, 0, 0, 0, 128, 128]
            } else {
                vec![255, 0, 0, 128]
            };
            writer
                .write_image_data(&sample.repeat(width as usize * height as usize))
                .unwrap();
            writer.finish().unwrap();
        }
        bytes
    }
    #[test]
    fn previews_html_as_plain_text_and_keeps_lossless_originals_after_restart() {
        let html = "<script>throw 'synthetic'</script>\r\n<img src=x onerror=alert(1)>\r\n";
        let text = format!("{html}{}", "中文 & # %\r\n".repeat(500));
        let outputs = CollectedOutputs::fixture("报告.md", text.as_bytes());
        let expected = outputs.files[0].manifest().clone();
        let (root, ws, request) = setup(outputs);
        drop(ws);
        let ws = Workspace::open(root.path()).unwrap();
        let preview = ws
            .experiment_output_preview(&request.operation_id, "报告.md")
            .unwrap();
        assert_eq!(preview.manifest, expected);
        let PreviewContent::Text { preview } = preview.content else {
            panic!()
        };
        assert!(preview.truncated);
        assert_eq!(preview.lines_shown, 200);
        assert!(preview.text.starts_with(html));
        assert!(text.starts_with(&preview.text));
        assert!(preview.text.len() <= MAX_TEXT);
        assert_eq!(
            ws.experiment_output(&request.operation_id, "报告.md")
                .unwrap()
                .content(),
            text.as_bytes()
        );
        let json = serde_json::to_value(
            ws.experiment_output_preview(&request.operation_id, "报告.md")
                .unwrap(),
        )
        .unwrap();
        assert_eq!(json["content"]["format"], "text");
        assert!(json["content"].get("html").is_none());
        let long = "中".repeat(MAX_TEXT);
        let bounded = text_preview(&long);
        assert!(
            bounded.truncated && bounded.text.len() <= MAX_TEXT && long.starts_with(&bounded.text)
        );
        assert_eq!(text_preview("").lines_shown, 0);
    }
    #[test]
    fn csv_previews_decode_quotes_crlf_and_embedded_lines_with_bounded_cells_and_columns() {
        let text =
            "\u{feff}name,value\r\n\"中文,逗号\",\"two\"\"quotes\"\r\n\"multi\r\nline\",18\r\n";
        let (_root, ws, request) = setup(CollectedOutputs::fixture("data.csv", text.as_bytes()));
        let PreviewContent::Csv { rows, truncated } = ws
            .experiment_output_preview(&request.operation_id, "data.csv")
            .unwrap()
            .content
        else {
            panic!()
        };
        assert!(!truncated);
        assert_eq!(
            rows,
            vec![
                vec!["name", "value"],
                vec!["中文,逗号", "two\"quotes"],
                vec!["multi\r\nline", "18"]
            ]
        );
        let wide = format!("{}\n", vec!["x"; 256].join(","));
        let tall = wide.repeat(300);
        let PreviewContent::Csv { rows, truncated } = csv_preview(&tall) else {
            panic!()
        };
        assert!(truncated);
        assert_eq!(rows.len(), MAX_LINES);
        assert!(rows.iter().all(|row| row.len() == MAX_COLUMNS));
        let big = format!("\"{}\",18", "中".repeat(10_000));
        let PreviewContent::Csv { rows, truncated } = csv_preview(&big) else {
            panic!()
        };
        assert!(truncated);
        assert!(rows[0][0].len() <= MAX_CELL);
        assert_eq!(rows[0][1], "18");
        let mixed = format!("{}中b,18", "a".repeat(MAX_CELL - 1));
        let PreviewContent::Csv { rows, truncated } = csv_preview(&mixed) else {
            panic!()
        };
        assert!(truncated);
        assert_eq!(rows[0][0], "a".repeat(MAX_CELL - 1));
        assert_eq!(rows[0][1], "18");
        let bytes = format!("{}\n", vec!["abcdef"; 64].join(",")).repeat(200);
        let PreviewContent::Csv { rows, truncated } = csv_preview(&bytes) else {
            panic!()
        };
        assert!(truncated);
        assert!(rows.iter().flatten().map(String::len).sum::<usize>() <= MAX_TEXT);
    }
    #[test]
    fn png_thumbnail_has_real_pixels_bounded_dimensions_and_an_unchanged_source_manifest() {
        let bytes = png_bytes(1024, 512, png::BitDepth::Sixteen);
        let outputs = CollectedOutputs::fixture("plot.png", &bytes);
        let expected = outputs.files[0].manifest().clone();
        let (_root, ws, request) = setup(outputs);
        let preview = ws
            .experiment_output_preview(&request.operation_id, "plot.png")
            .unwrap();
        assert_eq!(preview.manifest, expected);
        let PreviewContent::Png {
            width,
            height,
            original_width,
            original_height,
            content_base64,
        } = preview.content
        else {
            panic!()
        };
        assert_eq!(
            (width, height, original_width, original_height),
            (512, 256, 1024, 512)
        );
        let thumbnail = STANDARD.decode(content_base64).unwrap();
        assert!(thumbnail.len() <= 2 * 1024 * 1024);
        let mut reader = png::Decoder::new(std::io::Cursor::new(thumbnail))
            .read_info()
            .unwrap();
        let mut pixels = vec![0; reader.output_buffer_size().unwrap()];
        let info = reader.next_frame(&mut pixels).unwrap();
        assert_eq!(info.bit_depth, png::BitDepth::Eight);
        assert_eq!(info.color_type, png::ColorType::Rgba);
        assert!(pixels
            .chunks_exact(4)
            .all(|pixel| pixel == [255, 0, 0, 128]));
        assert_eq!(
            ws.experiment_output(&request.operation_id, "plot.png")
                .unwrap()
                .content(),
            bytes
        );
    }
    #[test]
    fn source_snapshots_survive_edits_moves_deletion_and_byte_chunks_split_utf8_losslessly() {
        let (root, mut ws, request) = setup(CollectedOutputs::fixture("report.txt", b"result"));
        let original = std::fs::read(root.path().join(&request.entry.path)).unwrap();
        let mut bytes = Vec::new();
        let mut offset = 0;
        loop {
            let chunk = ws
                .experiment_source_read(&request.operation_id, &request.entry.path, offset, 1)
                .unwrap();
            assert_eq!(chunk.source.hash, hash(&original));
            bytes.extend(STANDARD.decode(chunk.content_base64).unwrap());
            if let Some(next) = chunk.next_offset {
                offset = next;
            } else {
                break;
            }
        }
        assert_eq!(bytes, original);
        let changed = ws
            .write(
                &request.entry.path,
                &request.entry.hash,
                b"# user edit\r\n",
                "local",
            )
            .unwrap();
        ws.rename(&request.entry.path, "experiments/moved.py", &changed.hash)
            .unwrap();
        let preview = ws
            .experiment_source_preview(&request.operation_id, &request.entry.path)
            .unwrap();
        assert_eq!(preview.preview.text.as_bytes(), original);
        assert_eq!(
            preview.current_path.as_deref(),
            Some("experiments/moved.py")
        );
        assert_eq!(preview.source.path, request.entry.path);
        ws.delete("experiments/moved.py", &changed.hash).unwrap();
        drop(ws);
        let ws = Workspace::open(root.path()).unwrap();
        let preview = ws
            .experiment_source_preview(&request.operation_id, &request.entry.path)
            .unwrap();
        assert!(preview.current_path.is_none());
        assert_eq!(preview.preview.text.as_bytes(), original);
        assert_eq!(
            ws.experiment_source_read(
                &request.operation_id,
                &request.entry.path,
                original.len(),
                1
            )
            .unwrap()
            .content_base64,
            ""
        );
        assert!(ws
            .experiment_source_read(
                &request.operation_id,
                &request.entry.path,
                original.len() + 1,
                1
            )
            .is_err());
        for limit in [0, 256 * 1024 + 1] {
            assert!(ws
                .experiment_source_read(&request.operation_id, &request.entry.path, 0, limit)
                .is_err());
        }
        assert!(ws
            .experiment_source_preview(&request.operation_id, "../other.py")
            .is_err());
    }
    #[test]
    fn previews_refuse_corrupted_retained_bytes_foreign_vaults_and_forgotten_records() {
        let (_root, mut ws, request) =
            setup(CollectedOutputs::fixture("report.json", b"{\"value\":18}"));
        let original_vault = ws.vault_id.clone();
        ws.vault_id = uuid::Uuid::new_v4().to_string();
        assert_eq!(
            ws.experiment_source_preview(&request.operation_id, &request.entry.path)
                .unwrap_err()
                .code,
            "VAULT_PERMISSION_CHANGED"
        );
        assert_eq!(
            ws.experiment_output_preview(&request.operation_id, "report.json")
                .unwrap_err()
                .code,
            "VAULT_PERMISSION_CHANGED"
        );
        ws.vault_id = original_vault;
        let mut changed = std::fs::read(ws.root.join(&request.entry.path)).unwrap();
        changed[0] = b'!';
        ws.db
            .execute(
                "UPDATE experiment_inputs SET content=?2 WHERE operation_id=?1",
                params![request.operation_id, changed],
            )
            .unwrap();
        assert_eq!(
            ws.experiment_source_preview(&request.operation_id, &request.entry.path)
                .unwrap_err()
                .code,
            "EXPERIMENT_RECORD_CORRUPT"
        );
        ws.db
            .execute(
                "UPDATE experiment_outputs SET content=?2 WHERE operation_id=?1",
                params![request.operation_id, b"{\"value\":99}".as_slice()],
            )
            .unwrap();
        assert_eq!(
            ws.experiment_output_preview(&request.operation_id, "report.json")
                .unwrap_err()
                .code,
            "EXPERIMENT_RECORD_CORRUPT"
        );
        let record = ws
            .experiment_record(&request.operation_id)
            .unwrap()
            .unwrap();
        ws.experiment_forget(&request.operation_id, &record.summary.fingerprint)
            .unwrap();
        assert_eq!(
            ws.experiment_source_preview(&request.operation_id, &request.entry.path)
                .unwrap_err()
                .code,
            "EXPERIMENT_RECORD_FORGOTTEN"
        );
        assert_eq!(
            ws.experiment_output_preview(&request.operation_id, "report.json")
                .unwrap_err()
                .code,
            "EXPERIMENT_RECORD_FORGOTTEN"
        );
    }
}
