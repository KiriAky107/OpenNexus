import { nextTick, onBeforeUnmount, shallowRef } from 'vue'

export interface ActionDialogRequest { message: string; mode: 'confirm' | 'prompt'; initialValue: string }

/** 请求属于调用视图；离开它会取消待处理的工作。 */
export function useActionDialog() {
  const actionDialog = shallowRef<ActionDialogRequest | null>(null)
  let pending: ((value: string | null) => void) | undefined
  let disposed = false
  async function resolveAction(value: string | null) {
    const resolve = pending
    pending = undefined
    actionDialog.value = null
    await nextTick() // 在调用者继续之前恢复焦点并释放模式。
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
