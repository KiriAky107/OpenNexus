<script setup lang="ts">
import { t } from '@/i18n'
withDefaults(defineProps<{ busy?: boolean; identityLocked?: boolean; submitDisabled?: boolean }>(), { busy: false, identityLocked: false, submitDisabled: false })
const endpoint = defineModel<string>('endpoint', { required: true })
const account = defineModel<string>('account', { required: true })
const password = defineModel<string>('password', { required: true })
const device = defineModel<string>('device', { required: true })
defineEmits<{ login: [] }>()
</script>

<template>
  <form class="sync-form" @submit.prevent="$emit('login')">
    <label>{{ t('服务器地址', 'Server URL') }}<input v-model="endpoint" required :disabled="identityLocked || busy" type="url" autocomplete="url" /></label>
    <label>{{ t('账户', 'Account') }}<input v-model="account" required :disabled="identityLocked || busy" autocomplete="username" /></label>
    <label>{{ t('密码', 'Password') }}<input v-model="password" required type="password" autocomplete="current-password" :disabled="busy" /></label>
    <label>{{ t('设备名称', 'Device name') }}<input v-model="device" required maxlength="100" :disabled="busy" /></label>
    <slot />
    <button class="button-primary" :disabled="busy || submitDisabled || !password">{{ t('登录', 'Sign in') }}</button>
  </form>
</template>

<style scoped>
.sync-form { display: grid; grid-template-columns: repeat(2,minmax(0,1fr)); gap: 12px; margin-top: 16px; min-width: 0; }
label { display: grid; gap: 5px; min-width: 0; }
input { min-width: 0; min-height: 38px; padding: 8px; border: 1px solid var(--color-border-default); border-radius: 6px; background: var(--color-surface-primary); color: inherit; }
button { min-height: 38px; padding: 9px 14px; border: 1px solid transparent; border-radius: var(--radius-md); background: var(--color-accent-primary); color: var(--color-text-inverse); cursor: pointer; }
button:disabled { cursor: default; opacity: .5; }
@media(max-width:700px) { .sync-form { grid-template-columns: 1fr; } }
@media(pointer:coarse) { input, button { min-height: 44px; } }
</style>
