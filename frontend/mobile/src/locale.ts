export type Language = 'zh-CN' | 'en'
export const languageKey = 'opennexus.mobile.language'
export function savedLanguage(): Language {
  try { const value = localStorage.getItem(languageKey); if (value === 'en' || value === 'zh-CN') return value } catch { /* Session preference still works. */ }
  return navigator.language.toLowerCase().startsWith('zh') ? 'zh-CN' : 'en'
}
const en = {
  language: 'Interface language', ready: 'Local vault opened', checking: 'Opening the local vault…', retry: 'Try again',
  failed: 'Could not open the local vault', notes: 'Notes', drafts: 'Recover drafts', pending: 'Check operations', newNote: 'New note',
  search: 'Search names and text', find: 'Search', root: 'Vault', previous: 'Previous', next: 'Next',
  noNotes: 'No saved notes here', noDrafts: 'No unfinished drafts', noPending: 'No operations need checking',
  back: 'Back', save: 'Save', saving: 'Saving…', saved: 'Saved', draftKept: 'Draft kept on this device', unkept: 'Changes not yet kept', input: 'Composing text…',
  undo: 'Undo', redo: 'Redo', more: 'More actions', create: 'Create and edit', path: 'Note path', cancel: 'Cancel',
  copy: 'Keep as a new note', rename: 'Rename', delete: 'Delete', discard: 'Discard draft', body: 'Markdown note body',
  createTitle: 'New Markdown note', copyTitle: 'Keep the current text in a new note', renameTitle: 'Review a new note path',
  pathHelp: 'Use a relative path, such as notes/idea.md. A missing .md extension is added.',
  draftHelp: 'The draft is kept locally. Select Save in the editor to write the Markdown note.',
  review: 'Review action', confirm: 'Apply reviewed action', original: 'Original operation', check: 'Check original result',
  retryOriginal: 'Retry original content', cancelOriginal: 'Cancel original intent', resultPending: 'Completion is not yet confirmed.',
  completed: 'Completed', cancelled: 'Cancelled', rejected: 'Requires a new review', prepared: 'Awaiting confirmation',
  currentVersion: 'Current saved content', details: 'Operation details', loading: 'Reading…',
  frozen: 'The path and content hash below belong to the reviewed action. A later change will be rejected.',
  deleteTitle: 'Review deletion', deleteHelp: 'This removes the saved note from this vault.', discardTitle: 'Discard this local draft?',
  discardHelp: 'The saved Markdown note is kept. Uncommitted draft text will be discarded.',
  close: 'Close', open: 'Open', refresh: 'Refresh', retryDraft: 'Keep draft again', missing: 'The original note is no longer available; keep the draft as a new note.',
  storage: 'Notes and recovery drafts are stored in this app’s private storage.', skipped: 'Large notes were searched by name only.',
  reviewAgain: 'Read current content again', needsCheck: 'Save result needs checking', savedVersion: 'Saved version',
}
const zh: Record<keyof typeof en, string> = {
  language: '界面语言', ready: '本机知识库已打开', checking: '正在打开本机知识库…', retry: '重试',
  failed: '无法打开本机知识库', notes: '笔记', drafts: '恢复草稿', pending: '核对操作', newNote: '新建笔记',
  search: '搜索名称与正文', find: '搜索', root: '知识库', previous: '上一页', next: '下一页',
  noNotes: '这里还没有已保存的笔记', noDrafts: '没有待恢复的草稿', noPending: '没有待核对的操作',
  back: '返回', save: '保存', saving: '正在保存…', saved: '已保存', draftKept: '草稿已保留在本机', unkept: '更改尚未保留', input: '正在输入…',
  undo: '撤销', redo: '重做', more: '更多操作', create: '创建并编辑', path: '笔记路径', cancel: '取消',
  copy: '保留为新笔记', rename: '改名', delete: '删除', discard: '丢弃草稿', body: 'Markdown 笔记正文',
  createTitle: '新建 Markdown 笔记', copyTitle: '将当前正文保留到新笔记', renameTitle: '审阅新的笔记路径',
  pathHelp: '使用相对路径，例如 笔记/想法.md。未填写 .md 扩展名时会自动添加。',
  draftHelp: '草稿会保留在本机。请在编辑页点“保存”，将其写入 Markdown 笔记。',
  review: '审阅操作', confirm: '执行已审阅的操作', original: '原操作编号', check: '核对原操作结果',
  retryOriginal: '按原内容重试', cancelOriginal: '取消原意图', resultPending: '尚未确认操作完成。',
  completed: '已完成', cancelled: '已取消', rejected: '需要重新审阅', prepared: '待确认',
  currentVersion: '当前已保存的正文', details: '操作详情', loading: '正在读取…',
  frozen: '以下路径和内容摘要属于本次已审阅的操作。之后发生的变更会被拒绝。',
  deleteTitle: '审阅删除', deleteHelp: '这会从本知识库中移除已保存的笔记。', discardTitle: '丢弃此本地草稿？',
  discardHelp: '已保存的 Markdown 笔记会保留；尚未提交的草稿正文会被丢弃。',
  close: '关闭', open: '打开', refresh: '刷新', retryDraft: '重新保留草稿', missing: '原笔记已不可用，可以将草稿保留为新笔记。',
  storage: '笔记和恢复草稿保存在此应用的私有存储中。', skipped: '较大笔记仅搜索了名称。',
  reviewAgain: '重新读取当前正文', needsCheck: '保存结果待核对', savedVersion: '已保存版本',
}
export const text = (language: Language) => language === 'zh-CN' ? zh : en
const errors: Record<string, [string, string]> = {
  MOBILE_HOST_REQUIRED: ['请在 OpenNexus Android 应用中打开。', 'Open this in the OpenNexus Android app.'],
  MOBILE_NOTES_UNAVAILABLE: ['此宿主尚不支持本机编辑，请使用对应的新版本。', 'This Host does not support local editing; use the corresponding updated app.'],
  MOBILE_HOST_INCOMPATIBLE: ['应用与宿主不兼容。', 'The app and native Host are incompatible.'],
  QUOTA_EXCEEDED: ['存储空间不足，请释放空间后重试。', 'Storage is full. Free space and try again.'],
  REVISION_CONFLICT: ['已保存的内容发生了变化。请审阅当前版本，或将草稿保留为新笔记。', 'Saved content has changed. Review it or keep your draft as a new note.'],
  FILE_IDENTITY_CHANGED: ['原笔记的位置已改变，请重新审阅。', 'The original note moved. Review the action again.'],
  FILE_NOT_FOUND: ['原笔记已不可用。', 'The original note is no longer available.'],
  DRAFT_NOT_FOUND: ['原草稿已不可用，请保留当前正文为新笔记。', 'The original draft is unavailable. Keep the current text as a new note.'],
  DRAFT_SESSION_CHANGED: ['编辑状态已改变；当前输入仍在此页，可保留为新笔记。', 'The editor session changed. Your current text remains here and can be kept as a new note.'],
  DRAFT_GENERATION_CONFLICT: ['草稿已有更新，请核对后再保存。', 'The draft has a newer update. Check it before saving.'],
  DRAFT_PATH_IN_USE: ['此路径已有本地草稿，请先从恢复草稿中打开。', 'A local draft already uses this path. Open it from Recover drafts.'],
  FILE_ALREADY_EXISTS: ['此路径已有文件，请选择另一个名称。', 'A file already uses this path. Choose another name.'],
  PATH_CONFLICT: ['目标路径已被占用，请重新审阅。', 'The destination is already in use. Review another path.'],
  DRAFT_REQUIRES_SAVE: ['请先保存或处理当前草稿。', 'Save or resolve the current draft first.'],
  PENDING_SAVE_REQUIRES_REVIEW: ['先核对原保存操作，再决定是否按原内容重试。', 'Check the original save before deciding whether to retry its content.'],
  OPERATION_RECOVERY_REQUIRED: ['原操作仍需恢复，请先核对，不要另建操作。', 'The original operation requires recovery. Check it before creating another action.'],
  INPUT_COMPOSITION_ACTIVE: ['请先完成当前输入。', 'Finish the current text composition first.'],
  MOBILE_DRAFT_INVALID: ['正文超过当前支持的 1 MiB 上限，或草稿版本无效。', 'The draft exceeds the current 1 MiB limit or its version is invalid.'],
  CORE_NOTE_TOO_LARGE: ['此笔记超过当前 1 MiB 的编辑上限。', 'This note exceeds the current 1 MiB editing limit.'],
  MOBILE_HOST_INTERRUPTED: ['尚未确认保存结果，请先核对原操作。', 'The save result is not confirmed. Check the original operation first.'],
}
export function errorText(code: string, language: Language) {
  return errors[code]?.[language === 'zh-CN' ? 0 : 1] ?? (language === 'zh-CN' ? '未能完成操作。请保留当前正文，再核对或重试。' : 'The action did not complete. Keep the current text, then check or retry.')
}
