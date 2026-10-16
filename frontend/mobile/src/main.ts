import { createApp } from 'vue'
import App from './App.vue'
import '../../src/styles/tokens.css'
import '../../src/styles/features.css'
import './style.css'
import { initializeMobileTheme } from './theme'

initializeMobileTheme()
createApp(App).mount('#app')
