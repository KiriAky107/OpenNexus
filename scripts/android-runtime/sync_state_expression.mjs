// Fixed public controls/codes only. Never read input values, tokens or note text.
export function readSyncState() {
  const names = ['Read current connection', 'Read remote vaults', 'Read local association', 'Preview merge',
    'Confirm merge and bind', 'Confirm this association', 'Read queue and original records', 'Sync one cycle now', 'Stop this cycle']
  const allowed = new Set(['SYNC_RESPONSE_INVALID', 'SYNC_NETWORK_ERROR', 'SYNC_CONNECTION_FAILED', 'SYNC_AUTH_SELECTION_CHANGED',
    'SYNC_AUTH_IDENTITY_CHANGED', 'SYNC_AUTH_RECONCILIATION_REQUIRED', 'SYNC_SESSION_EXPIRED', 'SYNC_LOGIN_REQUIRED',
    'SYNC_FOREGROUND_CHANGED', 'SYNC_FOREGROUND_REQUIRED', 'SYNC_ALREADY_BOUND', 'SYNC_PREVIEW_CHANGED', 'SYNC_PREVIEW_PAGE_INVALID',
    'SYNC_BINDING_REQUEST_INVALID', 'SYNC_BINDING_PAGE_INVALID', 'SYNC_BINDING_INTENT_INVALID', 'SYNC_BINDING_INTENT_NOT_FOUND',
    'SYNC_BINDING_INTENT_CHANGED', 'SYNC_OPERATION_MISMATCH', 'SYNC_WORKER_INTERRUPTED', 'VAULT_CHANGED', 'HOST_BUSY',
    'MOBILE_STORAGE_UNAVAILABLE', 'MOBILE_HOST_REQUIRED', 'SYNC_PLATFORM_UNSUPPORTED', 'QUOTA_EXCEEDED', 'FILESYSTEM_ERROR', 'DATABASE_ERROR',
    'SYNC_CANCELLED', 'SYNC_PAUSED', 'SYNC_NOT_BOUND', 'SYNC_TARGET_INVALID', 'SYNC_ACTIVITY_BUSY', 'SYNC_FAILED'])
  const all = Array.from(document.querySelectorAll('button')).slice(0, 256)
  const buttons = names.map(name => ({ name, items: all.filter(e => e.textContent.trim() === name).slice(0, 3).map(e => {
    const r = e.getBoundingClientRect(), hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2)
    const style = getComputedStyle(e)
    return { left: r.left, top: r.top, width: r.width, height: r.height,
      enabled: !e.disabled, visible: r.width > 0 && r.height > 0 && style.display !== 'none' && style.visibility !== 'hidden',
      centerHitsOriginal: hit === e || e.contains(hit),
      centerTag: !hit ? null : ['BUTTON','SPAN','DIV','P','SELECT','SVG','PATH','INPUT'].includes(hit.tagName) ? hit.tagName : 'OTHER' }
  }) }))
  const publicErrors = Array.from(document.querySelectorAll('.association [role="alert"],.mobile-sync-cycle [role="alert"]')).slice(0, 8).map(e => {
    const match = e.textContent.match(/\(([A-Z][A-Z0-9_]{0,79})\)\s*$/)
    return match && allowed.has(match[1]) ? match[1] : 'UNRECOGNIZED_PUBLIC_ERROR'
  })
  return { visibility: document.visibilityState, documentFocused: document.hasFocus(),
    viewport: { width: innerWidth, height: innerHeight, dpr: devicePixelRatio },
    associationPresent: document.querySelector('.association') !== null,
    associationBusy: document.querySelector('.association')?.getAttribute('aria-busy') === 'true',
    mergePreviewPresent: document.querySelector('.sync-preview') !== null,
    cyclePresent: document.querySelector('.mobile-sync-cycle') !== null,
    publicErrors, buttons, inputValuesRead: false, rawErrorOrNoteTextReturned: false, mutationPerformed: false }
}
export const syncStateExpression = `(${readSyncState.toString()})()`
