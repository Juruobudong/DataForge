import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import router from './router'
import '@vue-flow/core/dist/style.css'
import '@vue-flow/controls/dist/style.css'
import '@vue-flow/minimap/dist/style.css'
import './styles-v7.css'

createApp(App).use(createPinia()).use(router).mount('#app')
