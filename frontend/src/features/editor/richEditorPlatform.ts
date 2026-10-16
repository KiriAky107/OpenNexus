import type { CSSProperties } from 'vue'
import type { MarkdownPreferences } from '@/stores/markdownPreferences'
import type { CommandHandler, CommandResult, EditorCommandId } from '@/services/editorCommandService'

/** The shared writing view receives document/platform capabilities, never a desktop host. */
export interface RichEditorPlatform {
  document: {
    content: string; currentFilePath: string | null; currentNoteId: string | null; mode: string; saveStatus: string
    headingRequest: { path: string | null; index: number } | null
    referenceRequest: { path: string | null; raw: string; occurrence: number } | null
    blockRequest: { path: string | null; offset: number; length: number } | null
    captureDocument(): () => boolean
    updateContent(content: string): void
    scheduleAutoSave(interval: number): void
  }
  settings: { spellCheck: boolean; language: string; autoSaveInterval: number }
  theme: { isDark: boolean; currentThemeId: string; resolvedCodeBlockTheme: 'github-light' | 'github-dark' }
  layout: { editorToolbarVisible: boolean; focusMode: boolean }
  headings: { preferences: { custom: boolean }; cssVariables: CSSProperties }
  markdown: MarkdownPreferences
  assets: {
    resolvePath(notePath: string, reference: string): string | null
    read(path: string, notePath: string, noteId: string | null): Promise<Blob>
    store?(file: File, source: 'upload' | 'paste' | 'drop', path: string, noteId: string | null): Promise<{ reference: string; original_name: string }>
    requestImage?(): void
    remoteImages: boolean
  }
  commands: {
    register(target: { available: () => boolean; handlers: Partial<Record<EditorCommandId, CommandHandler>> }): () => void
    execute(id: EditorCommandId, params?: unknown): Promise<CommandResult>
  }
  openLink(href: string): unknown
  documentChanged?(): void
}
