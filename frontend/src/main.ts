import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import router from './router'
import './styles/tokens.css'
import './styles/features.css'
import { useThemeStore } from './stores/theme'

const app = createApp(App)
const pinia = createPinia()

app.use(pinia)
app.use(router)

const themeStore = useThemeStore()
// initTheme 先同步落内置主题兜底，自定义主题恢复是异步的，不阻塞挂载。
void themeStore.initTheme()

app.mount('#app')
