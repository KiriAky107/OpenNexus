<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import type { PluginSettingsSchema, PluginSettingField } from '@/contracts'
import {
  getPluginSettings,
  updatePluginSettings,
  putPluginSecret,
  deletePluginSecret,
} from '@/services/pluginService'

const props = defineProps<{
  pluginId: string
}>()

const emit = defineEmits<{
  (e: 'saved'): void
  (e: 'error', message: string): void
}>()

const schema = ref<PluginSettingsSchema | null>(null)
const values = reactive<Record<string, unknown>>({})
const secrets = reactive<Record<string, string>>({})
const isLoading = ref(false)
const isSaving = ref(false)
const saveError = ref('')
const hasChanges = ref(false)
let editVersion = 0
let loadVersion = 0

const nonSecretFields = computed(() =>
  schema.value?.fields.filter((f) => f.type !== 'secret') ?? []
)

const secretFields = computed(() =>
  schema.value?.fields.filter((f) => f.type === 'secret') ?? []
)

async function load() {
  const version = ++loadVersion
  const pluginId = props.pluginId
  isLoading.value = true
  isSaving.value = false
  saveError.value = ''
  schema.value = null
  Object.keys(secrets).forEach(key => delete secrets[key])
  try {
    const loaded = await getPluginSettings(pluginId)
    if (version !== loadVersion) return
    schema.value = loaded
    Object.keys(values).forEach((k) => delete values[k])
    Object.assign(values, schema.value.values)
    hasChanges.value = false
    editVersion = 0
  } catch (error) {
    if (version === loadVersion) emit('error', error instanceof Error ? error.message : '设置加载失败')
  } finally {
    if (version === loadVersion) isLoading.value = false
  }
}

async function save() {
  if (!schema.value || isSaving.value) return
  const version = loadVersion
  const submittedEditVersion = editVersion
  const pluginId = props.pluginId
  isSaving.value = true
  saveError.value = ''
  try {
    const saved = await updatePluginSettings(
      pluginId,
      schema.value.schema_version,
      { ...values }
    )
    if (version !== loadVersion) return
    schema.value = saved
    hasChanges.value = editVersion !== submittedEditVersion
    emit('saved')
  } catch (error) {
    if (version === loadVersion) saveError.value = error instanceof Error ? error.message : '保存失败'
  } finally {
    if (version === loadVersion) isSaving.value = false
  }
}

async function saveSecret(key: string) {
  if (!schema.value || !secrets[key] || isSaving.value) return
  const version = loadVersion
  const pluginId = props.pluginId
  const submittedSecret = secrets[key]
  isSaving.value = true
  saveError.value = ''
  try {
    const result = await putPluginSecret(pluginId, key, submittedSecret)
    if (version !== loadVersion || pluginId !== props.pluginId) return
    if (schema.value) {
      schema.value.secrets[key] = { configured: result.configured }
    }
    if (secrets[key] === submittedSecret) secrets[key] = ''
    emit('saved')
  } catch (error) {
    if (version === loadVersion && pluginId === props.pluginId) saveError.value = error instanceof Error ? error.message : '密钥保存失败'
  } finally {
    if (version === loadVersion && pluginId === props.pluginId) isSaving.value = false
  }
}

async function clearSecret(key: string) {
  if (!schema.value || isSaving.value) return
  if (!confirm(`确认删除 " ${key} " 的配置？`)) return
  const version = loadVersion
  const pluginId = props.pluginId
  isSaving.value = true
  saveError.value = ''
  try {
    await deletePluginSecret(pluginId, key)
    if (version !== loadVersion || pluginId !== props.pluginId) return
    if (schema.value) {
      schema.value.secrets[key] = { configured: false }
    }
    emit('saved')
  } catch (error) {
    if (version === loadVersion && pluginId === props.pluginId) saveError.value = error instanceof Error ? error.message : '删除失败'
  } finally {
    if (version === loadVersion && pluginId === props.pluginId) isSaving.value = false
  }
}

function setFieldValue(key: string, value: unknown, field: PluginSettingField) {
  if (field.type === 'number') {
    const num = Number(value)
    if (field.minimum != null && num < field.minimum) return
    if (field.maximum != null && num > field.maximum) return
    values[key] = num
  } else {
    values[key] = value
  }
  hasChanges.value = true
  editVersion++
}

onMounted(load)
onBeforeUnmount(() => { loadVersion++ })
watch(() => props.pluginId, load)
</script>

<template>
  <div class="plugin-settings-panel">
    <div v-if="isLoading" class="loading">加载设置中…</div>

    <template v-else-if="schema && schema.fields.length > 0">
      <div v-if="saveError" class="error-banner small">{{ saveError }}</div>

      <div v-if="nonSecretFields.length" class="settings-section">
        <h4>通用设置</h4>
        <div class="form-grid">
          <div v-for="field in nonSecretFields" :key="field.key" class="field">
            <label>
              {{ field.label }}
              <span v-if="field.required" class="required">*</span>
            </label>
            <small v-if="field.description">{{ field.description }}</small>

            <input
              v-if="field.type === 'string'"
              :value="values[field.key] ?? ''"
              class="input"
              @input="setFieldValue(field.key, ($event.target as HTMLInputElement).value, field)"
            />

            <input
              v-else-if="field.type === 'number'"
              type="number"
              :value="values[field.key] ?? field.default ?? 0"
              :min="field.minimum ?? undefined"
              :max="field.maximum ?? undefined"
              class="input"
              @input="setFieldValue(field.key, ($event.target as HTMLInputElement).value, field)"
            />

            <label v-else-if="field.type === 'boolean'" class="switch-label">
              <input
                type="checkbox"
                :checked="Boolean(values[field.key] ?? field.default)"
                @change="setFieldValue(field.key, ($event.target as HTMLInputElement).checked, field)"
              />
              <span class="switch-track"><span class="switch-thumb"></span></span>
              <span class="switch-text">{{ values[field.key] ? '已启用' : '已禁用' }}</span>
            </label>

            <select
              v-else-if="field.type === 'select'"
              :value="String(values[field.key] ?? field.default ?? '')"
              class="select"
              @change="setFieldValue(field.key, ($event.target as HTMLSelectElement).value, field)"
            >
              <option v-for="opt in field.options" :key="opt" :value="opt">
                {{ opt }}
              </option>
            </select>
          </div>
        </div>

        <div class="form-actions">
          <button
            class="button-primary"
            :disabled="!hasChanges || isSaving"
            @click="save"
          >
            {{ isSaving ? '保存中…' : '保存设置' }}
          </button>
          <span v-if="hasChanges" class="unsaved-hint">有未保存的更改</span>
        </div>
      </div>

      <div v-if="secretFields.length" class="settings-section">
        <h4>密钥与凭据</h4>
        <p class="section-hint">密钥加密存储，前端不会回显明文。</p>
        <div class="form-grid">
          <div v-for="field in secretFields" :key="field.key" class="field secret-field">
            <label>{{ field.label }}</label>
            <small v-if="field.description">{{ field.description }}</small>
            <div class="secret-row">
              <span
                class="secret-status"
                :class="schema.secrets[field.key]?.configured ? 'configured' : 'not-configured'"
              >
                {{ schema.secrets[field.key]?.configured ? '● 已配置' : '○ 未配置' }}
              </span>
              <template v-if="schema.secrets[field.key]?.configured">
                <input
                  v-model="secrets[field.key]"
                  type="password"
                  placeholder="重新输入以更新"
                  class="input"
                />
                <button class="button-secondary" :disabled="!secrets[field.key] || isSaving" @click="saveSecret(field.key)">
                  更新
                </button>
                <button class="link-btn danger" :disabled="isSaving" @click="clearSecret(field.key)">清除</button>
              </template>
              <template v-else>
                <input
                  v-model="secrets[field.key]"
                  type="password"
                  placeholder="请输入密钥"
                  class="input"
                />
                <button
                  class="button-primary"
                  :disabled="!secrets[field.key] || isSaving"
                  @click="saveSecret(field.key)"
                >保存</button>
              </template>
            </div>
          </div>
        </div>
      </div>
    </template>

    <div v-else class="empty-hint">
      <p>此插件没有可配置项。</p>
    </div>
  </div>
</template>

<style scoped>
.plugin-settings-panel {
  display: grid;
  gap: var(--space-lg);
}

.settings-section h4 {
  margin-bottom: var(--space-sm);
  font-size: var(--font-size-md);
}

.section-hint {
  font-size: var(--font-size-sm);
  color: var(--color-text-tertiary);
  margin-bottom: var(--space-md);
}

.form-grid {
  display: grid;
  gap: var(--space-md);
}

.field {
  display: grid;
  gap: 4px;
}

.field label {
  font-size: var(--font-size-sm);
  color: var(--color-text-primary);
  font-weight: 500;
}

.field small {
  color: var(--color-text-tertiary);
  font-size: var(--font-size-xs);
}

.required {
  color: var(--color-error);
  margin-left: 2px;
}

.input, .select {
  padding: 6px 10px;
  border: 1px solid var(--color-border-default);
  border-radius: var(--radius-sm);
  background: var(--color-surface-primary);
  color: var(--color-text-primary);
  font-size: var(--font-size-sm);
  width: 100%;
  transition: border-color var(--motion-fast);
}

.input:focus, .select:focus {
  outline: none;
  border-color: var(--color-border-focus);
  box-shadow: 0 0 0 2px color-mix(in srgb, var(--color-accent-primary) 15%, transparent);
}

.switch-label {
  display: flex;
  align-items: center;
  gap: var(--space-sm);
  cursor: pointer;
  font-weight: 400 !important;
}

.switch-label input { display: none; }

.switch-track {
  position: relative;
  width: 40px;
  height: 22px;
  border-radius: 11px;
  background: var(--color-background-tertiary);
  transition: background-color var(--motion-fast);
}

.switch-thumb {
  position: absolute;
  top: 2px;
  left: 2px;
  width: 18px;
  height: 18px;
  border-radius: 50%;
  background: var(--color-text-inverse);
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.2);
  transition: transform var(--motion-fast);
}

.switch-label input:checked + .switch-track {
  background: var(--color-accent-primary);
}

.switch-label input:checked + .switch-track .switch-thumb {
  transform: translateX(18px);
}

.switch-text {
  font-size: var(--font-size-sm);
  color: var(--color-text-secondary);
}

.form-actions {
  display: flex;
  align-items: center;
  gap: var(--space-md);
  margin-top: var(--space-md);
}

.unsaved-hint {
  font-size: var(--font-size-xs);
  color: var(--color-warning);
}

.secret-field .secret-row {
  display: flex;
  align-items: center;
  gap: var(--space-sm);
  margin-top: 4px;
}

.secret-status {
  font-size: var(--font-size-xs);
  padding: 2px 8px;
  border-radius: var(--radius-full);
  white-space: nowrap;
}

.secret-status.configured {
  background: var(--color-success-soft);
  color: var(--color-success);
}

.secret-status.not-configured {
  background: var(--color-background-tertiary);
  color: var(--color-text-tertiary);
}

.secret-row .input {
  flex: 1;
  min-width: 0;
}

.error-banner.small {
  padding: var(--space-sm) var(--space-md);
  font-size: var(--font-size-sm);
}

.loading, .empty-hint {
  padding: var(--space-xl);
  text-align: center;
  color: var(--color-text-tertiary);
  font-size: var(--font-size-sm);
}

.link-btn {
  background: none;
  border: none;
  color: var(--color-text-secondary);
  cursor: pointer;
  font-size: var(--font-size-sm);
  padding: 0;
}
.link-btn.danger { color: var(--color-error); }
.link-btn:hover { text-decoration: underline; }
</style>
