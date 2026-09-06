import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import path from 'node:path'
import { readFileSync } from 'node:fs'

const mathChunks = new Map<string, string>()
function mathChunk(module: string) {
  const marker = '/node_modules/katex/'
  const root = module.slice(0, module.lastIndexOf(marker) + marker.length)
  if (!mathChunks.has(root)) {
    const { version } = JSON.parse(readFileSync(`${root}package.json`, 'utf8'))
    mathChunks.set(root, `math-katex-${version.replace(/[^0-9a-z]/gi, '-')}`)
  }
  return mathChunks.get(root)!
}

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, 'src'),
    },
  },
  build: {
    manifest: true,
    rollupOptions: {
      output: {
        // Keep lazy languages/diagrams independent; do not collect every vendor into one bundle.
        onlyExplicitManualChunks: true,
        manualChunks(id) {
          const module = id.replace(/\\/g, '/')
          if (!module.includes('/node_modules/')) return
          if (/\/@codemirror\/(?:state|view|language|commands|search|autocomplete|lint)\//.test(module) || /\/@lezer\/(?:common|highlight|lr)\//.test(module) || module.includes('/node_modules/codemirror/')) return 'editor-codemirror'
          if (/\/node_modules\/prosemirror-[^/]+\//.test(module)) return 'editor-prosemirror'
          if (module.includes('/node_modules/@milkdown/')) return 'editor-milkdown'
          if (module.includes('/node_modules/katex/')) return mathChunk(module)
        },
      },
    },
  },
  server: {
    host: '127.0.0.1',
    port: 5173,
    proxy: {
      '/api': 'http://127.0.0.1:8000',
      '/health': 'http://127.0.0.1:8000',
    },
  },
})
