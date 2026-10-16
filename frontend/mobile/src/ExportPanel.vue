<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { mobileExportDelivery, mobileRequest, mobileSaveExport } from './native'
import { errorCode } from './noteSession'
import { errorText, text, type Language } from './locale'
import type { ExportDelivery, ExportReceipt, Page } from './types'
const props = defineProps<{ language: Language }>()
const emit = defineEmits<{ close: []; language: [value: Language] }>()
const copy = computed(() => text(props.language)), page = ref<Page<ExportReceipt>>({items:[],total:0}), offset = ref(0)
const snapshot = ref<ExportReceipt | null>(null), delivery = ref<ExportDelivery | null>(null)
const busy = ref(false), error = ref(''), checking = ref(false)
let disposed = false, revision = 0
const number = (value: number) => new Intl.NumberFormat(props.language).format(value)
const date = (value: number) => new Intl.DateTimeFormat(props.language,{dateStyle:'medium',timeStyle:'short'}).format(new Date(value))
async function load() {
  page.value = await mobileRequest<Page<ExportReceipt>>({kind:'list_exports',offset:offset.value,limit:20})
}
async function perform(action: (current: () => boolean) => Promise<void>) {
  if (busy.value) return
  const owner = ++revision, current = () => !disposed && owner === revision
  busy.value = true;error.value = ''
  try { await action(current) } catch (cause) { if (current()) error.value = errorCode(cause) }
  finally { if (current()) busy.value = false }
}
async function select(value: ExportReceipt) {
  await perform(async current => {
    const original = await mobileRequest<ExportReceipt>({kind:'export_status',ticket:value.ticket})
    if (!current()) return
    snapshot.value = original;delivery.value = null;checking.value = true
    const result = await mobileExportDelivery(original.ticket)
    if (current()) { delivery.value=result;checking.value=false }
  })
}
async function freeze() {
  await perform(async current => {
    try {
      const result=await mobileRequest<ExportReceipt>({kind:'prepare_export'})
      if (current()) { snapshot.value=result;delivery.value={ticket:result.ticket,state:'not_started'};checking.value=false }
    } catch (cause) {
      // A lost preparation reply is recovered from the original sealed tickets, never auto-prepared again.
      await load();throw cause
    }
    await load()
  })
}
async function check() {
  const original=snapshot.value;if (!original) return
  await perform(async current => {
    const result=await mobileExportDelivery(original.ticket,delivery.value?.delivery_id)
    if (current()) { delivery.value=result;checking.value=false }
  })
}
async function save() {
  const original=snapshot.value;if (!original || checking.value) return
  await perform(async current => {
    // Preserve an unknown original before asking for an explicitly new destination.
    const observed=await mobileExportDelivery(original.ticket,delivery.value?.delivery_id)
    if (!current()) return
    delivery.value=observed
    const result=await mobileSaveExport(original.ticket)
    if (current()) delivery.value=result
  })
}
async function turn(delta: number) { await perform(async () => { offset.value=Math.max(0,offset.value+20*delta);await load() }) }
onMounted(() => { void perform(async () => { await load() }) })
onBeforeUnmount(() => { disposed=true;revision++ })
defineExpose({ handleBack() { if (!busy.value) emit('close'); return true } })
</script>

<template>
  <div class="dialog-backdrop">
    <section class="mobile-dialog export-panel" role="dialog" aria-modal="true" aria-labelledby="export-title" :aria-busy="busy">
      <label class="language dialog-language"><span class="sr-only">{{ copy.language }}</span><select :value="language" :aria-label="copy.language" @change="emit('language',($event.target as HTMLSelectElement).value as Language)"><option value="zh-CN">中文</option><option value="en">English</option></select></label>
      <h2 id="export-title">{{ copy.exportContent }}</h2>
      <p>{{ copy.exportScope }}</p><p>{{ copy.exportLimits }}</p>
      <div class="button-row"><button class="quiet" :disabled="busy" @click="emit('close')">{{ copy.close }}</button><button :disabled="busy" @click="freeze">{{ copy.freezeExport }}</button></div>
      <p v-if="error" role="alert">{{ errorText(error,language) }} <small>{{ error }}</small></p>
      <section v-if="snapshot" class="export-snapshot">
        <h3>{{ copy.frozenExport }}</h3><p>{{ snapshot.filename }}</p>
        <dl><dt>{{ copy.original }}</dt><dd><code>{{ snapshot.ticket }}</code></dd><dt>SHA-256</dt><dd><code>{{ snapshot.sha256 }}</code></dd><dt>{{ copy.exportFiles }}</dt><dd>{{ number(snapshot.file_count) }} · {{ number(snapshot.size) }} B</dd></dl>
        <p v-if="checking || !delivery" role="status">{{ copy.exportNeedsCheck }}</p>
        <template v-else>
          <p role="status">{{ delivery.state==='saved' ? copy.exportVerified : delivery.state==='cancelled' ? copy.cancelled : delivery.state==='not_started' ? copy.exportNotStarted : copy.exportNeedsCheck }}</p>
          <p v-if="delivery.delivery_id"><code>{{ delivery.delivery_id }}</code></p>
          <p v-if="delivery.verified_at">{{ date(delivery.verified_at) }}</p>
          <p v-if="['unknown','writing','choosing'].includes(delivery.state)">{{ copy.exportUnknownHelp }}</p>
        </template>
        <div class="button-row"><button class="quiet" :disabled="busy" @click="check">{{ copy.check }}</button><button :disabled="busy || checking || !delivery" @click="save">{{ delivery && ['saved','unknown','writing','choosing'].includes(delivery.state) ? copy.exportNewCopy : copy.chooseDestination }}</button></div>
      </section>
      <h3>{{ copy.preparedExports }}</h3>
      <button v-for="value in page.items" :key="value.ticket" class="note-row" :disabled="busy" @click="select(value)"><strong>{{ value.filename }}</strong><span>{{ number(value.file_count) }} · {{ number(value.size) }} B</span><code>{{ value.sha256 }}</code></button>
      <p v-if="!busy && !page.total">{{ copy.noExports }}</p>
      <nav v-if="page.total>20" class="pagination"><button class="quiet" :disabled="busy || offset===0" @click="turn(-1)">{{ copy.previous }}</button><span>{{ number(offset+1) }} / {{ number(page.total) }}</span><button class="quiet" :disabled="busy || offset+20>=page.total" @click="turn(1)">{{ copy.next }}</button></nav>
    </section>
  </div>
</template>
