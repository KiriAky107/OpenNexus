<script setup lang="ts">
import { lockDialogScroll } from '@/components/common/dialogScroll'
import { onMounted, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { useChatPreferences, validAvatar } from '@/stores/chatPreferences'
import { t } from '@/i18n'
import { apiClient } from '@/services/apiClient'
import { useWorkspaceStore } from '@/stores/workspace'
import { isDesktop } from '@/services/platform/desktop'
interface GlobalPersona { version: number; revision?: string; name: string; system_prompt: string; dialogue_pairs: Array<{user:string;assistant:string}> }
const emit = defineEmits<{ close: [] }>()
const preferences = useChatPreferences()
const draft = reactive({ ...preferences.settings })
const error = ref('')
const remote = reactive<GlobalPersona>({version:0,name:'',system_prompt:'',dialogue_pairs:[]})
const workspace = useWorkspaceStore()
let loadedVault = workspace.vaultId
watch(() => workspace.vaultId, () => { if (isDesktop()) { ready.value = false; legacy.value = null; error.value = t('工作区已切换，请重新打开人设设置。', 'Workspace changed. Reopen persona settings.') } })
const ready = ref(false)
const saving = ref(false)
const legacy = ref<GlobalPersona | null>(null)
const legacyLoading = ref(false)
const legacyMessage = ref('')
const loading = ref(0)
const dialog = ref<HTMLDialogElement>()
const previousFocus = document.activeElement as HTMLElement | null
let restoreScroll: (() => void) | undefined
let active = true
const generations = { aiAvatar: 0, userAvatar: 0 }
async function loadGlobal() {
  error.value = ''; ready.value = false
  const vault = workspace.vaultId
  try { const result = await apiClient.get<GlobalPersona>('/api/settings/persona'); if (active && (!isDesktop() || vault === workspace.vaultId)) { Object.assign(remote,result); loadedVault = vault; ready.value = true } }
  catch { if (active) error.value = t('无法加载全局人设，请重试。', 'Could not load global persona. Retry.') }
}
async function previewLegacy() {
  if (!ready.value || saving.value || legacyLoading.value) return
  const vault = workspace.vaultId
  legacyLoading.value = true; legacyMessage.value = ''; legacy.value = null
  try {
    const result = await apiClient.get<{ available: boolean; persona: GlobalPersona | null }>('/api/settings/persona/legacy')
    if (!active || vault !== workspace.vaultId || loadedVault !== vault) return
    legacy.value = result.available ? result.persona : null
    if (!legacy.value) legacyMessage.value = t('没有可导入的旧全局人设。', 'No legacy global persona is available.')
  } catch { if (active && vault === workspace.vaultId) legacyMessage.value = t('无法读取旧人设，请重试。', 'Could not read the legacy persona. Retry.') }
  finally { legacyLoading.value = false }
}
function useLegacy() {
  if (!legacy.value || !ready.value || saving.value || loadedVault !== workspace.vaultId) return
  remote.name = legacy.value.name
  remote.system_prompt = legacy.value.system_prompt
  remote.dialogue_pairs = legacy.value.dialogue_pairs.map(pair => ({ ...pair }))
  legacy.value = null
  legacyMessage.value = t('已填入表单；点击保存才会写入当前工作区，旧数据保留。', 'Copied into the form. Save to write to this workspace; legacy data is retained.')
}
onMounted(() => { if (dialog.value) restoreScroll = lockDialogScroll(dialog.value); dialog.value?.showModal(); void loadGlobal() })
onBeforeUnmount(() => { active = false; dialog.value?.close(); restoreScroll?.(); previousFocus?.focus() })
async function chooseAvatar(event: Event, field: 'aiAvatar' | 'userAvatar') {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file) return
  const generation = ++generations[field]
  error.value = ''
  if (!['image/png','image/jpeg','image/webp'].includes(file.type) || file.size > 512 * 1024) {
    error.value = t('请选择不超过 512 KB 的 PNG、JPEG 或 WebP 图片。', 'Choose a PNG, JPEG or WebP image up to 512 KB.'); return
  }
  loading.value++
  try {
    const data = await new Promise<string>((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(String(reader.result)); reader.onerror = () => reject(new Error('read')); reader.readAsDataURL(file) })
    if (!validAvatar(data)) throw new Error('format')
    const image = new Image()
    image.src = data
    await image.decode()
    if (active && generation === generations[field]) draft[field] = data
  } catch { if (active && generation === generations[field]) error.value = t('图片无法读取，请重新选择。', 'Could not read the image. Choose another file.') }
  finally { loading.value-- }
}
function clearAvatar(field: 'aiAvatar' | 'userAvatar') { generations[field]++; draft[field] = '' }
async function save() {
  if (loading.value || saving.value || !ready.value || (isDesktop() && loadedVault !== workspace.vaultId)) return
  saving.value = true; error.value = ''
  try {
    const updated = await apiClient.put<GlobalPersona>('/api/settings/persona', JSON.parse(JSON.stringify(remote)))
    Object.assign(remote, updated)
    if (!active) return
    try { preferences.save({...draft,persona:'',presetDialogue:''}) }
    catch { error.value = t('全局人设已保存，但本机头像存储失败，请缩小图片后重试。', 'Global persona saved, but local avatars could not be saved. Reduce image sizes and retry.'); return }
    emit('close')
  } catch (reason) { if (active) error.value = reason instanceof Error ? reason.message : t('全局人设保存失败。', 'Could not save global persona.') }
  finally { saving.value = false }
}
</script>

<template>
  <dialog ref="dialog" class="modal persona-dialog" aria-labelledby="persona-title" @cancel.prevent="emit('close')" @click="($event.target === dialog) && emit('close')">
    <form @submit.prevent="save">
      <div class="persona-heading"><h2 id="persona-title">{{ t('人设与头像', 'Persona and avatars') }}</h2><button type="button" class="button-secondary" @click="emit('close')">{{ t('关闭', 'Close') }}</button></div>
      <p class="notice-banner">{{ isDesktop() ? t('工作区人设 · 随当前 Vault 同步，应用于此工作区的对话与智能体。旧全局人设不会自动导入。', 'Workspace persona · Syncs with this Vault and applies to its chats and agents. Legacy global personas are not imported automatically.') : t('全局人设 · 应用于连接此 AI Core 的所有对话与智能体。留空的提示词和对话示例不会拼入请求。', 'Global persona · Applies to all chats and agents connected to this AI Core. Empty prompts and examples are omitted.') }}</p>
      <p v-if="!ready" role="status">{{ t('正在加载全局设置', 'Loading global settings') }} <button type="button" class="button-secondary" @click="loadGlobal">{{ t('重试', 'Retry') }}</button></p>
      <section v-if="isDesktop()" class="legacy-persona">
        <button type="button" class="button-secondary" :disabled="!ready || saving || legacyLoading" @click="previewLegacy">{{ t('查看旧全局人设', 'Preview legacy global persona') }}</button>
        <p v-if="legacyMessage" role="status">{{ legacyMessage }}</p>
        <div v-if="legacy" class="item-card">
          <p>{{ t('预览旧数据；填入表单会替换当前草稿，保存后才生效。', 'Preview only. Copying replaces the current draft; changes take effect after saving.') }}</p>
          <strong>{{ legacy.name }}</strong><pre>{{ legacy.system_prompt }}</pre>
          <article v-for="(pair, index) in legacy.dialogue_pairs" :key="index"><pre>{{ pair.user }}</pre><pre>{{ pair.assistant }}</pre></article>
          <button type="button" class="button-secondary" :disabled="!ready || saving" @click="useLegacy">{{ t('填入当前表单', 'Copy into current form') }}</button>
        </div>
      </section>
      <fieldset :disabled="!ready || saving" class="persona-columns">
        <div class="persona-primary">
          <label class="field"><span>{{ t('人设名称', 'Persona name') }}</span><input v-model="remote.name" class="input" maxlength="128" :placeholder="t('例如：知识助理', 'For example: Knowledge assistant')" /></label>
          <label class="field"><span>{{ t('系统提示词', 'System prompt') }}</span><textarea v-model="remote.system_prompt" class="textarea persona-prompt" maxlength="16000" :placeholder="t('描述 AI 的身份、语气及回答要求；留空则不添加', 'Identity, tone and response requirements; leave blank to omit')" /></label>
        </div>
        <div class="persona-secondary">
          <details open class="ui-disclosure"><summary>{{ t('预设对话', 'Example dialogue') }}</summary>
            <div class="dialogue-pairs">
              <p class="subtle">{{ t('用成对对话示范回答风格，作为全局系统提示词的一部分。', 'Use dialogue pairs to demonstrate response style as part of the global system prompt.') }}</p>
              <article v-for="(pair,index) in remote.dialogue_pairs" :key="index" class="item-card">
                <label class="field"><span>{{ t('我', 'Me') }}</span><textarea v-model="pair.user" class="textarea" maxlength="8000" rows="2" /></label>
                <label class="field"><span>AI</span><textarea v-model="pair.assistant" class="textarea" maxlength="8000" rows="2" /></label>
                <button type="button" class="button-secondary" @click="remote.dialogue_pairs.splice(index,1)">{{ t('删除对话对', 'Remove pair') }}</button>
              </article>
              <button type="button" class="button-secondary" :disabled="remote.dialogue_pairs.length >= 20" @click="remote.dialogue_pairs.push({user:'',assistant:''})">＋ {{ t('添加对话对', 'Add dialogue pair') }}</button>
            </div>
          </details>
          <h3>{{ t('本机头像', 'Local avatars') }}</h3>
      <div class="persona-avatars">
        <div v-for="field in (['aiAvatar', 'userAvatar'] as const)" :key="field" class="item-card avatar-setting">
          <strong>{{ field === 'aiAvatar' ? t('AI 头像', 'AI avatar') : t('我的头像', 'My avatar') }}</strong>
          <img v-if="draft[field]" :src="draft[field]" :alt="field === 'aiAvatar' ? 'AI' : t('我', 'Me')" /><span v-else class="avatar-placeholder">{{ field === 'aiAvatar' ? 'AI' : t('我', 'Me') }}</span>
          <label class="button-secondary avatar-upload">{{ t('选择图片', 'Choose image') }}<input type="file" accept="image/png,image/jpeg,image/webp" :aria-label="field === 'aiAvatar' ? t('选择 AI 头像', 'Choose AI avatar') : t('选择我的头像', 'Choose my avatar')" @change="chooseAvatar($event, field)" /></label>
          <button type="button" class="button-secondary" @click="clearAvatar(field)">{{ t('恢复默认', 'Reset') }}</button>
        </div>
      </div>
        </div>
      </fieldset>
      <p v-if="error" class="error-banner" role="alert">{{ error }}</p>
      <div class="inline-actions persona-footer"><button class="button-primary" :disabled="loading > 0 || !ready || saving">{{ saving ? t('保存中…', 'Saving…') : loading ? t('读取图片中…', 'Reading image…') : t('保存', 'Save') }}</button><button type="button" class="button-secondary" @click="emit('close')">{{ t('取消', 'Cancel') }}</button></div>
    </form>
  </dialog>
</template>

<style scoped>
.legacy-persona pre { white-space: pre-wrap; overflow-wrap: anywhere; max-height: 180px; overflow: auto; }
.persona-dialog { width: min(1120px, calc(100vw - 32px)); max-height: calc(100dvh - 48px); box-sizing: border-box; margin: auto; overflow: auto; color: var(--color-text-primary); background: var(--color-surface-primary); }
.persona-columns { display: grid; grid-template-columns: minmax(0,1fr) minmax(0,1fr); gap: 24px; border: 0; margin: 0; padding: 0; min-width: 0; }
.persona-primary, .persona-secondary { min-width: 0; }
.persona-prompt { min-height: 420px; }
.dialogue-pairs { padding: 12px; display: grid; gap: 12px; max-height: 480px; overflow: auto; }
.persona-footer { position: sticky; bottom: -24px; padding: 16px 0; background: var(--color-surface-primary); justify-content: flex-end; border-top: 1px solid var(--color-border-default); }
@media (max-width: 760px) { .persona-columns { grid-template-columns: 1fr; } .persona-prompt { min-height: 240px; } }
.persona-dialog::backdrop { background: var(--color-background-overlay); }
.persona-heading { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.persona-dialog .field { margin-block: 16px; }
.persona-avatars { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; margin-block: 16px; }
.avatar-setting { display: flex; align-items: center; flex-wrap: wrap; gap: 12px; }
.avatar-setting strong { width: 100%; }
.avatar-setting img, .avatar-placeholder { width: 48px; height: 48px; border-radius: var(--radius-full); object-fit: cover; background: var(--color-accent-soft); display: grid; place-items: center; }
.avatar-upload { position: relative; overflow: hidden; cursor: pointer; }
.avatar-upload input { position: absolute; inset: 0; opacity: 0; width: 100%; cursor: pointer; }
.avatar-upload:focus-within { outline: 2px solid var(--color-border-focus); }
@media (max-width: 520px) { .persona-avatars { grid-template-columns: 1fr; } }
</style>
