import mermaid from 'mermaid'
import { computed } from 'vue'
import { useThemeStore } from '@/stores/theme'

export function mermaidThemeVariables(dark: boolean) {
  const style = typeof document === 'undefined' ? null : getComputedStyle(document.documentElement)
  const color = (name: string, fallback: string) => style?.getPropertyValue(`--color-${name}`).trim() || fallback
  const text = color('text-primary', dark ? '#e6edf3' : '#1f2328')
  const border = color('border-default', dark ? '#484f58' : '#d0d7de')
  const surface = color('surface-primary', dark ? '#161b22' : '#ffffff')
  const primary = color('accent-soft', dark ? '#30363d' : '#eef0ff')
  const line = color('text-secondary', dark ? '#b1bac4' : '#656d76')
  return {
    darkMode: dark, background: surface, primaryColor: primary, primaryTextColor: text, primaryBorderColor: border,
    secondaryColor: color('info-soft', primary), secondaryTextColor: text, secondaryBorderColor: border,
    tertiaryColor: color('success-soft', primary), tertiaryTextColor: text, tertiaryBorderColor: border,
    textColor: text, lineColor: line, mainBkg: primary, nodeBorder: border,
    clusterBkg: surface, clusterBorder: border, edgeLabelBackground: surface,
    actorBkg: primary, actorBorder: border, actorTextColor: text, actorLineColor: line,
    signalColor: line, signalTextColor: text, labelBoxBkgColor: surface, labelBoxBorderColor: border, labelTextColor: text,
    noteBkgColor: color('warning-soft', primary), noteTextColor: text, noteBorderColor: border,
    activationBkgColor: primary, activationBorderColor: border,
  }
}

function ensureInitialized(theme: 'light' | 'dark') {
    mermaid.initialize({
      startOnLoad: false,
      theme: 'base',
      themeVariables: mermaidThemeVariables(theme === 'dark'),
      securityLevel: 'strict',
      fontFamily: 'var(--font-ui-sans)',
      flowchart: { useMaxWidth: true, htmlLabels: true },
      sequence: { useMaxWidth: true },
      gantt: { useMaxWidth: true },
    })
}
let queue: Promise<unknown> = Promise.resolve()
function serialized<T>(work: () => Promise<T>): Promise<T> {
  const result = queue.then(work)
  queue = result.catch(() => {})
  return result
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

export function renderMermaid(source: string, options: { theme?: 'light' | 'dark'; mode?: 'interactive' | 'static' } = {}): Promise<MermaidRenderResult> {
  return serialized(() => renderMermaidNow(source, options))
}

async function renderMermaidNow(
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
  const mermaidTheme = computed<'light' | 'dark'>(() => themeStore.isDark ? 'dark' : 'light')
  const themeId = computed(() => themeStore.currentThemeId)
  return { mermaidTheme, themeId }
}

export async function validateMermaid(source: string): Promise<{ valid: boolean; error?: MermaidParseError }> {
  try {
    await serialized(async () => { ensureInitialized('light'); await mermaid.parse(source) })
    return { valid: true }
  } catch (error) {
    const message = error instanceof Error ? error.message : '未知错误'
    return { valid: false, error: { message } }
  }
}
