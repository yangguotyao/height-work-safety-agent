import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  base: '/ui/',
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://127.0.0.1:8000',
      '/worker-assistant': 'http://127.0.0.1:8000',
      '/project-knowledge': 'http://127.0.0.1:8000',
      '/dynamic-risks': 'http://127.0.0.1:8000',
      '/audit-documents': 'http://127.0.0.1:8000',
      '/audits': 'http://127.0.0.1:8000'
    }
  }
})
