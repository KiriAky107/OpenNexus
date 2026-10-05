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

fn text_preview(text: &str) -> TextPreview {
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
