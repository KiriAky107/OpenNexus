<script setup lang="ts">
import { onMounted, ref } from 'vue'
import apiClient from '@/services/apiClient'
import { t } from '@/i18n'
const props = defineProps<{ kind: 'skill' | 'plugin' }>()
const errors = ref<{ kind: string; id: string; message: string }[]>([])
const failure = ref('')
onMounted(async () => {
  try { errors.value = (await apiClient.get<{ items: typeof errors.value }>('/api/extensions/restore-errors')).items.filter(item => item.kind === props.kind) }
  catch { failure.value = t('无法读取扩展恢复状态。', 'Unable to read extension recovery status.') }
})
</script>
<template>
  <div v-if="errors.length || failure" class="notice-banner" role="status">
    <p v-if="failure">{{ failure }}</p>
    <p v-for="item in errors" :key="item.id">{{ item.id }}：{{ t('启动恢复未完成，请检查包文件并重新安装；原授权不会自动用于变更后的包。', 'Startup recovery failed. Check and reinstall the package; previous grants are not applied to changed packages.') }}</p>
  </div>
</template>
