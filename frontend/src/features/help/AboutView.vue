<script setup lang="ts">
import { onMounted, ref } from 'vue'
import appPackage from '../../../package.json'
import { t } from '@/i18n'
import { checkGithubRelease, openExternalUrl, type ReleaseCheck } from '@/services/releaseService'
import appLogoUrl from '@/assets/opennexus-logo.svg'

const result = ref<ReleaseCheck>()
const loading = ref(false)
const error = ref('')

async function check() {
  if (loading.value) return
  loading.value = true
  error.value = ''
  try { result.value = await checkGithubRelease() }
  catch (reason) { error.value = reason instanceof Error ? reason.message : String(reason) }
  finally { loading.value = false }
}

onMounted(check)
</script>

<template>
  <section class="feature-page about-page">
    <header class="about-brand"><img :src="appLogoUrl" alt="OpenNexus" /><div><h1>OpenNexus</h1><p>{{ t('本地优先、以 Vault 为边界的 AI 笔记与知识工作台。', 'A local-first AI notes and knowledge workspace organized around Vault boundaries.') }}</p></div></header>
    <article class="panel about-card">
      <h2>{{ t('关于', 'About') }}</h2>
      <dl><div><dt>{{ t('当前版本', 'Current version') }}</dt><dd>v{{ appPackage.version }}</dd></div><div><dt>{{ t('许可证', 'License') }}</dt><dd>MIT</dd></div><div><dt>{{ t('项目主页', 'Project') }}</dt><dd><button class="link-button" @click="openExternalUrl('https://github.com/KiriAky107/OpenNexus')">github.com/KiriAky107/OpenNexus</button></dd></div></dl>
    </article>
    <article class="panel about-card">
      <div class="section-head"><div><h2>{{ t('检查更新', 'Check for updates') }}</h2><p class="subtle">{{ t('从 OpenNexus 官方 GitHub Releases 检查稳定版和测试版，不会自动安装。', 'Checks stable and prerelease versions from the official OpenNexus GitHub Releases. Updates are never installed automatically.') }}</p></div><button class="button-primary" :disabled="loading" @click="check">{{ loading ? t('检查中…', 'Checking…') : t('重新检查', 'Check again') }}</button></div>
      <p v-if="error" class="error-banner" role="alert">{{ t('无法检查更新：', 'Could not check for updates: ') }}{{ error }}</p>
      <div v-else-if="result" class="release-result" role="status"><strong>{{ result.update_available ? t(`发现新版本 v${result.latest_version}`, `Version v${result.latest_version} is available`) : t('当前已是最新版本', 'You are up to date') }}</strong><span v-if="result.prerelease" class="badge">{{ t('测试版', 'Prerelease') }}</span><button class="button-secondary" @click="openExternalUrl(result.release_url)">{{ t('打开 Release 页面', 'Open release page') }}</button></div>
    </article>
  </section>
</template>

<style scoped>
.about-page{max-width:900px;margin:0 auto;padding:36px;overflow:auto}.about-brand{display:flex;align-items:center;gap:18px;margin-bottom:24px}.about-brand img{width:72px;height:72px}.about-brand h1,.about-brand p,.about-card h2,.about-card p{margin:0}.about-card{display:grid;gap:16px;padding:22px;margin-bottom:18px}.about-card dl{display:grid;gap:10px;margin:0}.about-card dl div{display:grid;grid-template-columns:120px minmax(0,1fr);gap:12px}.about-card dt{color:var(--color-text-secondary)}.about-card dd{margin:0}.section-head,.release-result{display:flex;align-items:center;justify-content:space-between;gap:16px}.release-result{justify-content:flex-start;flex-wrap:wrap}.link-button{padding:0;border:0;background:none;color:var(--color-text-link);cursor:pointer;font:inherit}.link-button:hover{text-decoration:underline}@media(max-width:620px){.about-page{padding:20px}.section-head{align-items:flex-start;flex-direction:column}}
</style>
