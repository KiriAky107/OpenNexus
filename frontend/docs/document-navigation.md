# Document navigation

`editor.loadFile(path)` owns document and tab activation. It returns a
`FileNavigation` receipt after committing both, or `null` if superseded. Callers
that navigate routes, select Canvas nodes or highlight references check
`receipt.isCurrent()` after every intervening await. They do not separately
activate the requested tab. File tree selection can indicate a pending request
while the active tab still names the displayed document.

Paths use the same vault-root format in the editor, tabs and tree. This includes
AI citations and search results supplied without the leading slash. Formatting
does not decode URLs, change filename case or authorize a filesystem path.
Markdown link resolution still validates traversal and decodes URL components.

Navigation allocates its request version before awaiting a save. Choosing the
current file invalidates another pending load. Workspace folder selection,
closing a tab and beginning a vault change invalidate in-flight requests too.
Cancelled reads do not set a tab, reset edit state or show an old error.

The current document stays editable while another document is being read. Its
dirty content and any edits made during an outstanding save are saved to the
old path before switching. The final dirty-state check, document replacement
and tab activation are synchronous. A save failure or revision conflict leaves
the old document and its unsaved content available. Loading progress is separate
from the document's save status.

Views capture their document identity when created. Late editor callbacks cannot
write into a newly loaded document, including reopening the same path. Background
external-content reads and save completions check that identity as well. Actual
external deletion still exposes recovery and preserves unsaved text.

Workspace refresh retains a pending body check independently of the file tree
hash. A temporary read failure, or a check skipped while saving, is retried on
the next workspace refresh even if the hash is unchanged. Only a successful
read or an explicit conflict acknowledges the body change. Failed reads show
an automatic-retry notice and retain the current editor text.

Regression coverage uses controlled reads and saves to verify out-of-order
completion, typing during loads and saves, returning to the current file,
closing or changing scope, failed saves, stale editor callbacks, citations and
Markdown anchors. Native release checks additionally exercise the packaged UI.
