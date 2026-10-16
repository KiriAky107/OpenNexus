import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { fileURLToPath } from 'node:url'

const root = fileURLToPath(new URL('.', import.meta.url))
const frontend = fileURLToPath(new URL('..', import.meta.url))

export default defineConfig({
  root,
  plugins: [vue()],
  worker: { format: 'es' },
  publicDir: false,
  resolve: { alias: { '@': fileURLToPath(new URL('../src', import.meta.url)) } },
  build: { outDir: 'dist', emptyOutDir: true, manifest: true },
  server: {
    host: process.env.TAURI_DEV_HOST || '127.0.0.1',
    port: 1420,
    strictPort: true,
    fs: { allow: [frontend] },
  },
})
