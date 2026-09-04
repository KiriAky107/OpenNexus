<script setup lang="ts">
import { ref, watch } from 'vue'
import { apiClient } from '@/services/apiClient'
import type { RequestOverride } from '@/contracts'
const props = defineProps<{modelValue: RequestOverride[]}>()
const emit = defineEmits<{ 'update:modelValue': [value:RequestOverride[]]; valid:[value:boolean] }>()
const transferError = ref('')
let published = JSON.stringify(props.modelValue)
let generation = 0
const rules = ref(props.modelValue.map(rule => ({...rule, draft: JSON.stringify(rule.body, null, 2), error: ''})))
const protectedFields = new Set(['model','messages','input','system','instructions','tools','tool_choice','parallel_tool_calls','functions','function_call','file','audio','reference_file','stream','previous_response_id','conversation','background','store'])
function publish() {
  generation++
  let valid = true
  const result: RequestOverride[] = []
  for (const rule of rules.value) {
    try {
      const body = JSON.parse(rule.draft)
      if (!body || typeof body !== 'object' || Array.isArray(body)) throw new Error('顶层必须为 JSON 对象')
      const conflicts = Object.keys(body).filter(key => protectedFields.has(key))
      if (conflicts.length) throw new Error(`运行请求管理字段不可覆盖：${conflicts.join(', ')}`)
      rule.error = ''
      result.push({capability:rule.capability,model:rule.model || null,stream:rule.stream ?? null,body})
    } catch(e) { rule.error = (e as Error).message; valid = false }
  }
  emit('valid', valid)
  if(valid) { published = JSON.stringify(result); emit('update:modelValue', result) }
}
function add() { rules.value.push({capability:'chat',model:null,stream:null,body:{},draft:'{}',error:''}); publish() }
function format(index:number) { try { rules.value[index].draft = JSON.stringify(JSON.parse(rules.value[index].draft), null, 2); publish() } catch { publish() } }
watch(() => props.modelValue, value => {
  if (JSON.stringify(value) !== published) {
    generation++
    rules.value = value.map(rule => ({...rule, draft: JSON.stringify(rule.body, null, 2), error: ''}))
    published = JSON.stringify(value)
    emit('valid', true)
  }
}, {deep: true})
function reset() { rules.value = []; transferError.value = ''; publish() }
async function importRules(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file) return
  const current = ++generation
  transferError.value = ''
  try {
    if (file.size > 1024 * 1024) throw new Error('配置文件不得超过 1 MiB')
    const parsed = JSON.parse(await file.text())
    const validated = await apiClient.post<{request_overrides: RequestOverride[]}>('/api/providers/request-rules/validate', parsed)
    if (current !== generation) return
    rules.value = validated.request_overrides.map(rule => ({...rule, draft: JSON.stringify(rule.body, null, 2), error: ''}))
    publish()
  } catch(e) { if (current === generation) transferError.value = (e as Error).message }
}
async function exportRules() {
  transferError.value = ''
  try {
    publish()
    if (rules.value.some(rule => rule.error)) throw new Error('请先修正 JSON')
    const validated = await apiClient.post('/api/providers/request-rules/validate', {version:1, request_overrides:JSON.parse(published)})
    const url = URL.createObjectURL(new Blob([JSON.stringify(validated, null, 2)], {type:'application/json'}))
    const link = document.createElement('a'); link.href = url; link.download = 'model-request-rules.json'; link.click()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  } catch(e) { transferError.value = (e as Error).message }
}

</script>
<template>
  <details class="request-json"><summary>高级：自定义请求 JSON</summary>
    <p class="subtle">提供商通用规则先应用，再应用模型规则。对象递归合并，数组整体替换，null 作为实际值；删除键后恢复继承。密钥继续使用独立 API Key 配置。</p>
    <div v-for="(rule,index) in rules" :key="index" class="rule">
      <div class="rule-selectors"><label>能力<select v-model="rule.capability" class="select" @change="publish"><option value="chat">聊天</option><option value="embedding">Embedding</option><option value="transcription">音频转写</option><option value="speaker_matching">声纹比对</option></select></label>
      <label>模型<input v-model="rule.model" class="input" placeholder="留空：全部模型" @input="publish" /></label>
      <label>请求模式<select v-model="rule.stream" class="select" @change="publish"><option :value="null">全部</option><option :value="true">仅流式</option><option :value="false">仅非流式</option></select></label></div>
      <textarea v-model="rule.draft" class="input json-body" rows="6" aria-label="自定义请求 JSON" spellcheck="false" placeholder='{"stream_options":{"include_usage":true}}' @input="publish" />
      <p v-if="rule.error" class="error-text" role="alert">{{ rule.error }}</p>
      <div class="inline-actions"><button type="button" class="button-secondary" @click="format(index)">格式化</button><button type="button" class="button-danger" @click="rules.splice(index,1); publish()">删除规则</button></div>
    </div>
    <button type="button" class="button-secondary" @click="add">添加请求规则</button>
    <div class="inline-actions"><button type="button" class="button-secondary" @click="reset">恢复默认请求</button><button type="button" class="button-secondary" @click="exportRules">导出请求配置</button><label>导入请求配置<input type="file" accept=".json" @change="importRules" /></label></div>
    <p v-if="transferError" class="error-text" role="alert">{{ transferError }}</p>
    <p class="subtle">导入替换当前请求规则，保存提供商后生效。导出仅包含请求规则，不包含凭据引用和 API Key。</p>
  </details>
</template>
<style scoped>.request-json{display:grid;gap:12px}.rule{padding:12px;border:1px solid var(--border-color);border-radius:8px;margin:12px 0}.rule-selectors{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:10px}.rule-selectors label{display:grid;gap:5px}.json-body{font-family:monospace;width:100%}</style>
