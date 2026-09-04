<script setup lang="ts">
import { ref, watch } from 'vue'
import type { RequestOverride } from '@/contracts'
const props = defineProps<{modelValue: RequestOverride[]}>()
const emit = defineEmits<{ 'update:modelValue': [value:RequestOverride[]]; valid:[value:boolean] }>()
const rules = ref(props.modelValue.map(rule => ({...rule, draft: JSON.stringify(rule.body, null, 2), error: ''})))
const protectedFields = new Set(['model','messages','input','system','instructions','tools','tool_choice','parallel_tool_calls','functions','function_call','file','audio','reference_file','stream','previous_response_id','conversation','background','store'])
function publish() {
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
  if(valid) emit('update:modelValue', result)
}
function add() { rules.value.push({capability:'chat',model:null,stream:null,body:{},draft:'{}',error:''}); publish() }
function format(index:number) { try { rules.value[index].draft = JSON.stringify(JSON.parse(rules.value[index].draft), null, 2); publish() } catch { publish() } }
watch(() => props.modelValue.length, length => { if (length === 0 && rules.value.length && rules.value.every(r => !r.error)) rules.value = [] })
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
  </details>
</template>
<style scoped>.request-json{display:grid;gap:12px}.rule{padding:12px;border:1px solid var(--border-color);border-radius:8px;margin:12px 0}.rule-selectors{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:10px}.rule-selectors label{display:grid;gap:5px}.json-body{font-family:monospace;width:100%}</style>
