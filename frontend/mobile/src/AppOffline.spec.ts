// @vitest-environment happy-dom
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { EditorView } from '@codemirror/view'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { installTestDialogApi } from './dialogTestSupport'
import App from './App.vue'
import MobilePreview from './MobilePreview.vue'
import type { Draft, DraftView, ImageIntent, MobileRequest, MutationIntent, NoteDocument, SaveIntent } from './types'

installTestDialogApi()

const host = vi.hoisted(() => ({ mobileCapabilities: vi.fn(), mobileVault: vi.fn(), mobileRequest: vi.fn(), mobileOpenExternal: vi.fn() }))
vi.mock('./native', () => host)
let wrapper: VueWrapper | undefined
let doc: NoteDocument, draft: Draft, pending: SaveIntent | null, mutation: MutationIntent | null, present: boolean
let image: ImageIntent | null
const clone = <T>(value: T): T => JSON.parse(JSON.stringify(value)) as T
const snapshot = (): DraftView => clone({ draft, current: draft.file_id && present ? doc : null, current_error: null, pending_save: pending, pending_mutation: mutation?.state === 'prepared' ? mutation : null, pending_image: image?.state === 'prepared' ? image : null })
async function api(request: MobileRequest): Promise<unknown> {
  switch (request.kind) {
    case 'list_notes': return { items: present ? [clone(doc)] : [], total: present ? 1 : 0, directories: [], unsearched_large_notes: 0 }
    case 'list_drafts': return { items: draft.generation !== draft.saved_generation || !draft.file_id ? [{ ...clone(draft), bytes: draft.content.length }] : [], total: draft.generation !== draft.saved_generation || !draft.file_id ? 1 : 0 }
    case 'list_pending': return { items: [], total: 0 }
    case 'open_note': case 'open_draft': case 'draft_status': return snapshot()
    case 'read_note': return clone(doc)
    case 'create_draft':
      draft = { id: 'new-draft', file_id: null, path: request.path, base_hash: '', content: request.content ?? '', generation: 1, saved_generation: 0, edit_session: 'new-lease' }
      return snapshot()
    case 'persist_draft': draft = { ...draft, generation: request.generation, content: request.content }; return clone(draft)
    case 'prepare_save':
      pending = { operation_id: 'original-native-save', draft_id: draft.id, file_id: draft.file_id, generation: draft.generation, path: draft.path, expected: draft.base_hash, content: draft.content, state: 'prepared', error: null }
      return clone(pending)
    case 'observe_save': return clone(pending)
    case 'apply_save': {
      const frozen = pending!; doc = { ...doc, path: frozen.path, content: frozen.content, hash: 'b'.repeat(64), revision: 2 }; present = true
      draft = { ...draft, file_id: doc.file_id, saved_generation: frozen.generation, base_hash: doc.hash }; pending = null
      return { ...frozen, state: 'committed' }
    }
    case 'prepare_mutation':
      mutation = { operation_id: 'original-native-file-action', kind: request.action, file_id: request.file_id, expected: request.expected, path: doc.path, destination: request.destination, state: 'prepared', error: null }
      return clone(mutation)
    case 'observe_mutation': return clone(mutation)
    case 'apply_mutation': mutation = { ...mutation!, state: 'committed' }; if (mutation.kind === 'delete') present = false; return clone(mutation)
    case 'cancel_mutation': mutation = { ...mutation!, state: 'cancelled' }; return clone(mutation)
    case 'discard_draft': return { discarded: true }
    case 'prepare_image':
      image = { operation_id: 'original-native-image', draft_id: draft.id, path: draft.path, generation: draft.generation,
        asset_path: 'attachments/ab/abcd.png', reference: 'attachments/ab/abcd.png', hash: 'abcd', size: 4,
        content_before: draft.content, content_after: draft.content.slice(0,request.from)+'![](attachments/ab/abcd.png)'+draft.content.slice(request.to),state:'prepared',error:null }
      return clone(image)
    case 'apply_image': image = { ...image!, state: 'committed' }; draft = { ...draft, content:image.content_after,generation:image.generation+1 }; return clone(image)
    case 'observe_image': return clone(image)
    default: throw Error(`Unexpected native request: ${request.kind}`)
  }
}
beforeEach(() => {
  localStorage.clear(); localStorage.setItem('opennexus.mobile.language', 'en'); vi.clearAllMocks()
  doc = { file_id: 'file-a', path: 'original.md', hash: 'a'.repeat(64), revision: 1, deleted: false, is_folder: false, content: 'original\r\n中文 😀' }
  draft = { id: 'draft-a', file_id: doc.file_id, path: doc.path, base_hash: doc.hash, generation: 0, saved_generation: 0, content: doc.content, edit_session: 'lease-a' }
  pending = null; mutation = null; image = null; present = true
  host.mobileCapabilities.mockResolvedValue({ protocol: 1, platform: 'android', workspace: true, notes: true, version: '0.1.0-dev1' })
  host.mobileVault.mockResolvedValue({ vault_id: 'owned-vault', note_count: 1 })
  host.mobileRequest.mockImplementation(api)
})
afterEach(() => { wrapper?.unmount(); wrapper = undefined })
async function start() { wrapper = mount(App, { attachTo: document.body }); await flushPromises(); return wrapper }
function button(label: string) { const found = wrapper!.findAll('button').find(b => b.text() === label); expect(found, label).toBeDefined(); return found! }
async function open() { await wrapper!.get('.tree-node[aria-label="original.md"]').trigger('click'); await flushPromises() }

it('opens a reviewed original external link only after the explicit confirmation',async()=>{
  host.mobileCapabilities.mockResolvedValue({protocol:1,platform:'android',workspace:true,notes:true,external_links:true,version:'0.1.0-dev1'})
  host.mobileOpenExternal.mockResolvedValue({state:'opened'})
  await start();await open();await wrapper!.get('.file-actions summary').trigger('click');await button('Read preview').trigger('click');await flushPromises()
  wrapper!.getComponent(MobilePreview).vm.$emit('link','https://example.com/original?a=%25');await flushPromises()
  expect(host.mobileOpenExternal).not.toHaveBeenCalled();expect(wrapper!.get('.mobile-dialog').text()).toContain('https://example.com/original?a=%25')
  await button('Open').trigger('click');await flushPromises();expect(host.mobileOpenExternal).toHaveBeenCalledExactlyOnceWith('https://example.com/original?a=%25')
  expect(doc.content).toBe('original\r\n中文 😀')
})

it('resolves a reviewed local link through a bounded native path read before switching the editor',async()=>{
  host.mobileRequest.mockImplementation(async (request:MobileRequest)=>{
    if(request.kind==='read_path')return {...doc,file_id:'linked-file',path:'dir/中文.md',content:'linked original'}
    if(request.kind==='open_note' && request.file_id==='linked-file'){
      draft={...draft,id:'linked-draft',file_id:'linked-file',path:'dir/中文.md',content:'linked original',edit_session:'linked-lease'}
      return snapshot()
    }
    return api(request)
  })
  await start();await open();await wrapper!.get('.file-actions summary').trigger('click');await button('Read preview').trigger('click');await flushPromises()
  wrapper!.getComponent(MobilePreview).vm.$emit('link','dir/%E4%B8%AD%E6%96%87.md');await flushPromises();await button('Open').trigger('click');await flushPromises()
  expect(host.mobileRequest.mock.calls.find(([r])=>r.kind==='read_path')?.[0]).toEqual({kind:'read_path',path:'dir/中文.md'})
  expect(wrapper!.get('.titlebar .file-name').text()).toBe('中文.md')
  expect(wrapper!.get('.cm-editor').text()).toContain('linked original')
})

it('imports the selected original image into the real editor at its raw cursor and keeps undo', async () => {
  await start(); await open()
  const original = doc.content, view = EditorView.findFromDOM(wrapper!.get('.cm-editor').element as HTMLElement)!
  view.dispatch({ selection: { anchor: view.state.doc.length } })
  await wrapper!.get('.file-actions summary').trigger('click'); await button('Add image').trigger('click')
  const input = wrapper!.get('input[type="file"]')
  Object.defineProperty(input.element,'files',{ value:[new File([new Uint8Array([1,2,3,4])],'chosen.png',{ type:'image/png' })],configurable:true })
  await input.trigger('change'); await flushPromises()
  expect(host.mobileRequest.mock.calls.find(([r])=>r.kind==='prepare_image')?.[0]).toMatchObject({ from:original.length,to:original.length,content_base64:'AQIDBA==' })
  expect(draft.content).toBe(original+'![](attachments/ab/abcd.png)');expect(doc.content).toBe(original)
  expect(wrapper!.get('.save-status').text()).toBe('Draft kept on this device')
  await button('Undo').trigger('click'); await flushPromises()
  expect(wrapper!.emitted('error')).toBeUndefined()
  expect(view.state.doc.toString()).toBe(original.replace(/\r\n/g,'\n'))
})

it('checks and discards only the local draft while leaving saved note bytes unchanged', async () => {
  await start(); await open()
  const original = doc.content
  const view = EditorView.findFromDOM(wrapper!.get('.cm-editor').element as HTMLElement)!
  view.dispatch({ changes: { from: view.state.doc.length, insert: ' unfinished' } }); await flushPromises()
  await wrapper!.get('.file-actions summary').trigger('click'); await button('Discard draft').trigger('click')
  const confirm = wrapper!.get('.mobile-dialog').findAll('button').find(b => b.text() === 'Discard draft')!
  await confirm.trigger('click'); await flushPromises()
  expect(wrapper!.find('.cm-editor').exists()).toBe(false); expect(doc.content).toBe(original)
  const actions = host.mobileRequest.mock.calls.map(([r]) => r.kind)
  expect(actions.indexOf('draft_status')).toBeLessThan(actions.indexOf('discard_draft'))
  expect(actions.includes('apply_save')).toBe(false)
})

it('wires real editor bytes through draft persistence and the original frozen save', async () => {
  await start(); await open()
  const view = EditorView.findFromDOM(wrapper!.get('.cm-editor').element as HTMLElement)!
  view.dispatch({ changes: { from: view.state.doc.length, insert: '\nmore' } }); await flushPromises()
  await button('Save').trigger('click'); await flushPromises()
  expect(wrapper!.get('.save-status').text()).toBe('Saved')
  expect(doc.content).toBe('original\r\n中文 😀\r\nmore')
  expect(host.mobileRequest.mock.calls.filter(([r]) => r.kind === 'apply_save').map(([r]) => r)).toEqual([{ kind: 'apply_save', operation_id: 'original-native-save' }])
})

it('preserves a frozen deletion across an accessible dialog language switch', async () => {
  await start(); await open(); await wrapper!.get('.file-actions summary').trigger('click')
  await button('Delete').trigger('click'); await flushPromises()
  expect(wrapper!.get('dialog').text()).toContain('original-native-file-action')
  expect(wrapper!.get('dialog').text()).toContain('original.md')
  const count = host.mobileRequest.mock.calls.length, frozen = clone(mutation)
  await wrapper!.get('.mobile-dialog select').setValue('zh-CN'); await flushPromises()
  expect(host.mobileRequest).toHaveBeenCalledTimes(count); expect(mutation).toEqual(frozen)
  expect(wrapper!.get('dialog').text()).toContain('审阅删除')
  await button('执行已审阅的操作').trigger('click'); await flushPromises()
  expect(host.mobileRequest.mock.calls.filter(([r]) => r.kind === 'apply_mutation').map(([r]) => r)).toEqual([{ kind: 'apply_mutation', operation_id: 'original-native-file-action' }])
})

it('creates a recoverable draft without claiming the Markdown file is saved', async () => {
  await start(); await button('New note').trigger('click')
  await wrapper!.get('.mobile-dialog input').setValue('新笔记')
  await button('Create and edit').trigger('click'); await flushPromises()
  expect(draft.path).toBe('新笔记.md'); expect(draft.file_id).toBeNull()
  expect(wrapper!.get('.save-status').text()).toBe('Draft kept on this device')
  expect(host.mobileRequest.mock.calls.some(([r]) => r.kind === 'apply_save')).toBe(false)
})

it('does not let an earlier failed search overwrite the current results', async () => {
  let rejectOld!: (reason: Error) => void
  const old = new Promise<unknown>((_, reject) => { rejectOld = reject })
  host.mobileRequest.mockImplementation((request: MobileRequest) => {
    if (request.kind === 'list_notes' && request.query === 'old') return old
    if (request.kind === 'list_notes' && request.query === 'latest') return Promise.resolve({ items: [{ ...doc, path: 'latest.md' }], total: 1, directories: [], unsearched_large_notes: 0 })
    return api(request)
  })
  await start(); await wrapper!.get('.file-search input').setValue('old'); await wrapper!.get('.file-search').trigger('submit'); await flushPromises()
  await wrapper!.get('.file-search input').setValue('latest'); await wrapper!.get('.file-search').trigger('submit'); await flushPromises()
  rejectOld(Error('earlier request failed')); await flushPromises()
  expect(wrapper!.get('.tree-node[aria-label="latest.md"]').text()).toContain('latest.md')
  expect(wrapper!.find('[role="alert"]').exists()).toBe(false)
})

it('checks read-only before an explicit retry of the same original save', async () => {
  let dropped = false
  host.mobileRequest.mockImplementation(async (request: MobileRequest) => {
    if (request.kind === 'apply_save' && !dropped) { dropped = true; throw Error('MOBILE_HOST_INTERRUPTED') }
    return api(request)
  })
  await start(); await open()
  const view = EditorView.findFromDOM(wrapper!.get('.cm-editor').element as HTMLElement)!
  view.dispatch({ changes: { from: view.state.doc.length, insert: ' changed' } }); await flushPromises()
  await button('Save').trigger('click'); await flushPromises()
  expect(wrapper!.get('.save-status').text()).toBe('Save result needs checking')
  await button('Check original result').trigger('click'); await flushPromises()
  expect(host.mobileRequest.mock.calls.filter(([r]) => r.kind === 'apply_save')).toHaveLength(1)
  await button('Retry original content').trigger('click'); await flushPromises()
  expect(host.mobileRequest.mock.calls.filter(([r]) => r.kind === 'apply_save').map(([r]) => r)).toEqual([
    { kind: 'apply_save', operation_id: 'original-native-save' }, { kind: 'apply_save', operation_id: 'original-native-save' },
  ])
  expect(wrapper!.get('.save-status').text()).toBe('Saved')
})
