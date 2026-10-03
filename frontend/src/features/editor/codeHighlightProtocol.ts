import type { CodeTheme } from '@/services/codeHighlighter'

export const MAX_HIGHLIGHT_CHARACTERS = 1_000_000
export const MAX_HIGHLIGHT_SPANS = 250_000
export interface HighlightInput { source: string; language: string; theme: CodeTheme }
export interface HighlightResult {
  /** Flat UTF-16 [from, to, style-index] triples, sorted by source position. */
  spans: Uint32Array
  styles: string[]
}
export interface HighlightRequest extends HighlightInput { id: number }
export interface HighlightResponse { id: number; result?: HighlightResult }

export function tokenStyle(color: string | undefined, fontStyle = 0): string {
  return `color:${color ?? 'inherit'};font-style:${fontStyle & 1 ? 'italic' : 'normal'};font-weight:${fontStyle & 2 ? 'bold' : 'normal'};text-decoration:${fontStyle & 4 ? 'underline' : 'none'}`
}
