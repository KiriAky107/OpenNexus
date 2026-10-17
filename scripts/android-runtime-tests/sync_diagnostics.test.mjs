import { afterEach, test } from 'node:test'
import assert from 'node:assert/strict'
import { readSyncState, syncStateExpression } from '../android-runtime/sync_state_expression.mjs'

const keys = ['document', 'getComputedStyle', 'innerWidth', 'innerHeight', 'devicePixelRatio']
const original = Object.fromEntries(keys.map(key => [key, globalThis[key]]))
afterEach(() => { for (const key of keys) { if (original[key] === undefined) delete globalThis[key]; else globalThis[key] = original[key] } })
function fixture(buttons = [], alerts = []) {
  globalThis.innerWidth = 390; globalThis.innerHeight = 700; globalThis.devicePixelRatio = 2
  globalThis.getComputedStyle = () => ({ display: 'block', visibility: 'visible' })
  globalThis.document = {
    visibilityState: 'visible', hasFocus: () => true,
    querySelector: selector => selector === '.association' ? { getAttribute: () => 'false' } : null,
    querySelectorAll: selector => {
      assert.ok(!selector.includes('input'), 'A credential input was queried')
      return selector === 'button' ? buttons : alerts
    },
    elementFromPoint: () => buttons[0] ?? null,
  }
}
function button(name, disabled = false) {
  const value = { textContent: name, disabled, tagName: 'BUTTON', contains: () => false,
    getBoundingClientRect: () => ({ left: 10, top: 20, width: 100, height: 44 }) }
  Object.defineProperty(value, 'value', { get() { throw Error('Input value read') } })
  return value
}
test('returns geometry and fixed labels without input values or unrelated button text', () => {
  fixture([button('Preview merge'), button('private-account-and-path')])
  const result = readSyncState(), encoded = JSON.stringify(result)
  assert.equal(result.buttons.find(row => row.name === 'Preview merge').items[0].centerHitsOriginal, true)
  assert.equal(encoded.includes('private-account-and-path'), false)
  assert.equal(result.inputValuesRead, false); assert.equal(result.mutationPerformed, false)
})
test('exports only known public codes and never raw alert content', () => {
  fixture([], [{ textContent: 'untrusted secret endpoint or note (SYNC_AUTH_SELECTION_CHANGED)' },
    { textContent: 'private raw failure (UNKNOWN_SECRET_VALUE)' }])
  const result = readSyncState(); assert.deepEqual(result.publicErrors, ['SYNC_AUTH_SELECTION_CHANGED', 'UNRECOGNIZED_PUBLIC_ERROR'])
  assert.equal(JSON.stringify(result).includes('private'), false)
  assert.equal(JSON.stringify(result).includes('untrusted secret'), false)
})
test('the same self-contained expression runs read-only with no profile or preview', () => {
  fixture(); document.visibilityState = 'hidden'; document.hasFocus = () => false
  const result = Function(`return ${syncStateExpression}`)()
  assert.equal(result.visibility, 'hidden'); assert.equal(result.documentFocused, false)
  assert.equal(result.mergePreviewPresent, false); assert.ok(result.buttons.every(row => row.items.length === 0))
})
