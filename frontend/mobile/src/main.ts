import { createApp } from 'vue'
import App from './App.vue'
import './style.css'
import { initializeMobileTheme } from './theme'

initializeMobileTheme()
createApp(App).mount('#app')
