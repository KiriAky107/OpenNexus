<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { getServiceStatus, type ServiceStatus } from './api'

const service = ref<ServiceStatus | null>(null)
const error = ref('')
const loading = ref(false)

async function checkBackend() {
  loading.value = true
  error.value = ''
  try {
    service.value = await getServiceStatus()
  } catch (reason) {
    service.value = null
    error.value = reason instanceof Error ? reason.message : '未知错误'
  } finally {
    loading.value = false
  }
}

onMounted(checkBackend)
</script>

<template>
  <main class="app-shell">
    <aside class="sidebar">
      <div class="brand">
        <span class="brand-mark">N</span>
        <span>Notes Agent</span>
      </div>

      <nav aria-label="主导航">
        <button class="nav-item active" type="button">工作台</button>
        <button class="nav-item" type="button" disabled>笔记</button>
        <button class="nav-item" type="button" disabled>AI 助手</button>
        <button class="nav-item" type="button" disabled>设置</button>
      </nav>

      <p class="sidebar-hint">Vue 3 + TypeScript</p>
    </aside>

    <section class="workspace">
      <header>
        <p class="eyebrow">LOCAL-FIRST AI NOTES</p>
        <h1>项目基础壳子</h1>
        <p class="subtitle">前端界面已经就绪，并通过统一 API Client 检查 FastAPI 服务。</p>
      </header>

      <div class="cards">
        <article class="card hero-card">
          <div>
            <p class="card-label">AI Core</p>
            <h2>后端连接状态</h2>
          </div>

          <div v-if="service" class="status-line success">
            <span class="status-dot" />
            <div>
              <strong>服务正常</strong>
              <p>{{ service.name }} · v{{ service.version }} · {{ service.environment }}</p>
            </div>
          </div>
          <div v-else-if="error" class="status-line error">
            <span class="status-dot" />
            <div>
              <strong>暂未连接</strong>
              <p>{{ error }}</p>
            </div>
          </div>
          <div v-else class="status-line">
            <span class="status-dot" />
            <p>正在检查服务…</p>
          </div>

          <button class="primary-button" type="button" :disabled="loading" @click="checkBackend">
            {{ loading ? '检查中…' : '重新检查' }}
          </button>
        </article>

        <article class="card">
          <p class="card-label">FRONTEND</p>
          <h2>Vue 3 + TypeScript</h2>
          <p>使用 Vite 启动，开发环境已配置后端代理。</p>
        </article>

        <article class="card">
          <p class="card-label">BACKEND</p>
          <h2>FastAPI + Pydantic</h2>
          <p>包含健康检查、状态接口和自动 OpenAPI 文档。</p>
        </article>
      </div>
    </section>
  </main>
</template>
