import mermaid from 'mermaid'
import { ref, watch } from 'vue'
import { useThemeStore } from '@/stores/theme'

let initialized = false
let initTheme: 'light' | 'dark' = 'light'

function ensureInitialized(theme: 'light' | 'dark') {
  if (!initialized) {
    mermaid.initialize({
      startOnLoad: false,
      theme: theme === 'dark' ? 'dark' : 'default',
      securityLevel: 'strict',
      fontFamily: 'var(--font-ui-sans)',
      flowchart: { useMaxWidth: true, htmlLabels: true },
      sequence: { useMaxWidth: true },
      gantt: { useMaxWidth: true },
    })
    initialized = true
    initTheme = theme
    return
  }
  if (initTheme !== theme) {
    mermaid.initialize({
      theme: theme === 'dark' ? 'dark' : 'default',
    })
    initTheme = theme
  }
}

export interface MermaidRenderResult {
  svg: string
  width: number
  height: number
  warnings: string[]
}

export interface MermaidParseError {
  message: string
  line?: number
  column?: number
}

let renderCounter = 0

export async function renderMermaid(
  source: string,
  options: { theme?: 'light' | 'dark'; mode?: 'interactive' | 'static' } = {}
): Promise<MermaidRenderResult> {
  const theme = options.theme ?? 'light'
  ensureInitialized(theme)
  const id = `mermaid-${Date.now()}-${++renderCounter}`
  try {
    const result = await mermaid.render(id, source)
    const parser = new DOMParser()
    const doc = parser.parseFromString(result.svg, 'image/svg+xml')
    const svg = doc.querySelector('svg')
    let width = 800
    let height = 600
    if (svg) {
      const viewBox = svg.getAttribute('viewBox')
      if (viewBox) {
        const parts = viewBox.split(/\s+/).map(Number)
        if (parts.length === 4) {
          width = parts[2]
          height = parts[3]
        }
      }
      const w = svg.getAttribute('width')
      const h = svg.getAttribute('height')
      if (w && !isNaN(parseFloat(w))) width = parseFloat(w)
      if (h && !isNaN(parseFloat(h))) height = parseFloat(h)
    }
    return {
      svg: result.svg,
      width,
      height,
      warnings: [],
    }
  } catch (error) {
    const message = error instanceof Error ? error.message : 'Mermaid 渲染失败'
    return {
      svg: renderErrorSvg(message),
      width: 400,
      height: 120,
      warnings: [message],
    }
  }
}

function renderErrorSvg(message: string): string {
  return `<svg xmlns="http://www.w3.org/2000/svg" width="400" height="120" viewBox="0 0 400 120">
  <rect width="400" height="120" fill="var(--color-error-soft, #ffebe9)" rx="6" />
  <text x="20" y="30" font-family="var(--font-ui-mono, monospace)" font-size="13" fill="var(--color-error, #cf222e)" font-weight="600">Mermaid 渲染错误</text>
  <text x="20" y="55" font-family="var(--font-ui-mono, monospace)" font-size="12" fill="var(--color-text-secondary, #656d76)">${escapeXml(message).slice(0, 100)}</text>
  <text x="20" y="90" font-family="var(--font-ui-sans, sans-serif)" font-size="11" fill="var(--color-text-tertiary, #9198a0)">请检查语法是否正确，支持 flowchart、sequenceDiagram、classDiagram 等。</text>
</svg>`
}

function escapeXml(str: string): string {
  return str.replace(/[<>&'"]/g, (c) => {
    const map: Record<string, string> = { '<': '&lt;', '>': '&gt;', '&': '&amp;', "'": '&apos;', '"': '&quot;' }
    return map[c] ?? c
  })
}

export function useMermaidTheme() {
  const themeStore = useThemeStore()
  const mermaidTheme = ref<'light' | 'dark'>(themeStore.isDark ? 'dark' : 'light')
  watch(() => themeStore.isDark, (isDark) => {
    mermaidTheme.value = isDark ? 'dark' : 'light'
    ensureInitialized(mermaidTheme.value)
  })
  return { mermaidTheme }
}

export async function validateMermaid(source: string): Promise<{ valid: boolean; error?: MermaidParseError }> {
  try {
    ensureInitialized('light')
    await mermaid.parse(source)
    return { valid: true }
  } catch (error) {
    const message = error instanceof Error ? error.message : '未知错误'
    return { valid: false, error: { message } }
  }
}
