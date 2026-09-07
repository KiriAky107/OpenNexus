import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import router from './router'
import './styles/tokens.css'
import './styles/features.css'
import './styles/markdown-behavior.css'
import { useThemeStore } from './stores/theme'
import { useSettingsStore } from './stores/settings'
import { watch } from 'vue'
import { appLocale } from './i18n'
import { updateDocumentTitle } from './router'
import { installDesktopLifecycle } from './services/platform/lifecycle'

const app = createApp(App)
const pinia = createPinia()

app.use(pinia)
app.use(router)

const themeStore = useThemeStore()
const settingsStore = useSettingsStore()
void themeStore.initTheme()
watch(appLocale, () => updateDocumentTitle())
watch(() => settingsStore.spellCheck, (enabled) => {
  document.body.spellcheck = enabled
  document.body.setAttribute('spellcheck', String(enabled))
}, { immediate: true })

app.mount('#app')
void installDesktopLifecycle()
