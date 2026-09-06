import { nextTick, onBeforeUnmount, shallowRef } from 'vue'

export interface ActionDialogRequest { message: string; mode: 'confirm' | 'prompt'; initialValue: string }

/** Requests belong to the invoking view; leaving it cancels pending work. */
export function useActionDialog() {
  const actionDialog = shallowRef<ActionDialogRequest | null>(null)
  let pending: ((value: string | null) => void) | undefined
  let disposed = false
  async function resolveAction(value: string | null) {
    const resolve = pending
    pending = undefined
    actionDialog.value = null
    await nextTick() // Restore focus and release the modal before the caller continues.
    resolve?.(disposed ? null : value)
  }
  function request(mode: ActionDialogRequest['mode'], message: string, initialValue = '') {
    if (disposed || pending) return Promise.resolve(null)
    actionDialog.value = { mode, message, initialValue }
    return new Promise<string | null>(resolve => { pending = resolve })
  }
  onBeforeUnmount(() => { disposed = true; pending?.(null); pending = undefined; actionDialog.value = null })
  return {
    actionDialog, resolveAction,
    askConfirm: async (message: string) => (await request('confirm', message)) !== null,
    askPrompt: (message: string, initialValue = '') => request('prompt', message, initialValue),
  }
}
