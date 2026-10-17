// Diagnostic-only in the one owned hosted debug WebView. No request is created,
// retried, rewritten, delayed or suppressed. Input values stay unread, and raw
// error text is never returned by the observer.
export function installAssociationObserver(observerId) {
  const key = '__onxOwnedAssociationObserver'
  const existing = window[key]
  if (existing) {
    if (existing.observerId !== observerId) throw Error('OWNED_OBSERVER_ALREADY_BOUND')
    return { installed: true, observerId, alreadyInstalled: true, callsAvailable: existing.callsAvailable }
  }
  if (!document.querySelector('#app')?.__vue_app__ || typeof window.__TAURI_INTERNALS__?.invoke !== 'function') {
    throw Error('OWNED_APP_NOT_READY')
  }
  const names = new Set(['Read current connection', 'Read remote vaults', 'Read local association', 'Preview merge',
    'Confirm merge and bind', 'Confirm this association', 'Read queue and original records', 'Sync one cycle now', 'Stop this cycle'])
  const actions = new Set(['status', 'preview', 'bind', 'operation', 'pending', 'resume'])
  const kinds = new Set(['status', 'preview', 'operation', 'pending'])
  const codes = new Set(['SYNC_RESPONSE_INVALID', 'SYNC_NETWORK_ERROR', 'SYNC_CONNECTION_FAILED',
    'SYNC_AUTH_SELECTION_CHANGED', 'SYNC_FOREGROUND_CHANGED', 'SYNC_FOREGROUND_REQUIRED', 'SYNC_PREVIEW_CHANGED',
    'SYNC_LOGIN_REQUIRED', 'SYNC_SESSION_EXPIRED', 'HOST_BUSY', 'VAULT_CHANGED'])
  const state = { observerId, events: [], calls: [], eventOverflow: 0, callOverflow: 0, callsAvailable: false }
  const time = () => Math.round(performance.now())
  for (const kind of ['pointerdown', 'pointerup', 'touchstart', 'touchend', 'click']) {
    document.addEventListener(kind, event => {
      const button = event.target?.closest?.('button')
      if (!button || !document.querySelector('#app')?.contains(button)) return
      const name = button.textContent?.trim()
      if (!names.has(name)) return
      if (state.events.length === 32) { state.eventOverflow++; return }
      state.events.push({ kind, name, trusted: event.isTrusted === true, at: time(),
        enabled: !button.disabled, associationBusy: document.querySelector('.association')?.getAttribute('aria-busy') === 'true' })
    }, true)
  }
  const internal = window.__TAURI_INTERNALS__, original = internal.invoke
  const descriptor = Object.getOwnPropertyDescriptor(internal, 'invoke')
  if (descriptor?.writable === true) {
    internal.invoke = function (command, args, options) {
      const action = command === 'mobile_sync_association' ? args?.request?.action : null
      let record = null
      if (actions.has(action)) {
        if (state.calls.length < 16) {
          record = { ordinal: state.calls.length + 1, action, at: time(), outcome: 'pending', replyKind: null, errorCode: null }
          state.calls.push(record)
        } else state.callOverflow++
      }
      const failed = error => {
        if (record) {
          const code = typeof error === 'string' ? error : error instanceof Error ? error.message : null
          record.outcome = 'rejected'; record.errorCode = codes.has(code) ? code : 'OTHER_ERROR'
        }
      }
      let result
      try { result = Reflect.apply(original, this, arguments) }
      catch (error) { failed(error); throw error }
      if (record && result && typeof result.then === 'function') {
        result.then(reply => {
          record.outcome = 'fulfilled'; record.replyKind = kinds.has(reply?.kind) ? reply.kind : 'OTHER_REPLY'
        }, failed)
      }
      return result
    }
    state.callsAvailable = internal.invoke !== original
  }
  Object.defineProperty(window, key, { value: state, configurable: false })
  return { installed: true, observerId, alreadyInstalled: false, callsAvailable: state.callsAvailable }
}

export function readAssociationObserver(observerId) {
  const state = window.__onxOwnedAssociationObserver
  if (!state || state.observerId !== observerId) return { installed: false, observerId, documentOrObserverChanged: true }
  return { installed: true, observerId, callsAvailable: state.callsAvailable, events: state.events, calls: state.calls,
    eventOverflow: state.eventOverflow, callOverflow: state.callOverflow,
    inputValuesOrRawErrorsReturned: false, nativeRequestsCreatedOrRetried: false }
}
