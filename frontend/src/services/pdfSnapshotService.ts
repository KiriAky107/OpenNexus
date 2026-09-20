import { apiClient } from './apiClient'
import { renderMarkdown } from '@/utils/markdown'
import { splitNoteMetadata } from '@/utils/noteMetadata'
import { t } from '@/i18n'
import { mermaidThemeVariables } from './mermaidService'
import { useThemeStore } from '@/stores/theme'
import { useMarkdownPreferencesStore } from '@/stores/markdownPreferences'
import { useHeadingAppearanceStore } from '@/stores/headingAppearance'
// 仅加载编辑器及 Markdown 组件的样式，包括 Vue scoped 规则，不额外挂载编辑器实例。
import MarkdownContent from '@/components/common/MarkdownContent.vue'
import VisualMarkdownEditor from '@/features/editor/VisualMarkdownEditor.vue'
void MarkdownContent; void VisualMarkdownEditor

interface Resources { images: {source:string; data:string|null; warnings:string[]}[]; plots: {source:string; svg:string; warnings:string[]}[] }
interface Options { theme_id:string; include_title:boolean; page_size:string }
type SnapshotFormat = 'html' | 'pdf'

const printRules = `
@page { margin: 0; }
html, body { margin:0 !important; padding:0 !important; width:auto !important; height:auto !important; min-height:0 !important; overflow:visible !important; display:block !important; }
* { -webkit-print-color-adjust:exact !important; print-color-adjust:exact !important; animation:none !important; transition:none !important; }
.pdf-document, .pdf-document .milkdown-host, .pdf-document .milkdown { display:block !important; height:auto !important; min-height:0 !important; overflow:visible !important; }
.pdf-document .ProseMirror { min-height:0 !important; overflow:visible !important; box-decoration-break:clone; -webkit-box-decoration-break:clone; }
.pdf-document :is(h1,h2,h3,h4,h5,h6) { break-after:avoid; }
.pdf-document img { max-width:100%; }
.pdf-document .markdown-mermaid > svg { width:100% !important; min-width:0 !important; max-width:100% !important; height:auto !important; max-height:250mm; }
.pdf-document :is(.markdown-mermaid,.markdown-math,table) { break-inside:avoid; }
.pdf-document :is(pre,.shiki) { overflow:visible !important; white-space:pre-wrap; overflow-wrap:anywhere; }
.pdf-document .markdown-code-toolbar button, .pdf-document .diagram-controls { display:none !important; }
`

function attrs(element: Element): string {
  return [...element.attributes].filter(a => a.name==='class' || a.name==='style' || a.name.startsWith('data-')).map(a=>` ${a.name}="${escape(a.value)}"`).join('')
}
function escape(text: string) { return text.replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/</g,'&lt;').replace(/>/g,'&gt;') }
function scopeAttributes(component: unknown) { const id=(component as {__scopeId?:string}).__scopeId; return id ? ` ${id}` : '' }
async function dataUrl(url: string, signal?:AbortSignal):Promise<string> {
  const response=await fetch(url,{signal}); if(!response.ok) throw Error(`导出资源读取失败：${url}`)
  const blob=await response.blob()
  return await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result));reader.onerror=reject;reader.readAsDataURL(blob)})
}
export function preferWoff2FontSource(css:string):string {
  if(!/^\s*@font-face\b/i.test(css))return css
  const declaration=/\bsrc\s*:/i.exec(css)
  if(!declaration)return css
  const valueStart=declaration.index+declaration[0].length
  let quote='',depth=0,valueEnd=-1
  for(let index=valueStart;index<css.length;index+=1) {
    const character=css[index]!
    if(quote) {
      if(character==='\\')index+=1
      else if(character===quote)quote=''
    } else if(character==='"'||character==="'")quote=character
    else if(character==='(')depth+=1
    else if(character===')')depth=Math.max(0,depth-1)
    else if(character===';'&&depth===0){valueEnd=index;break}
  }
  if(valueEnd<0)return css
  const sources=css.slice(valueStart,valueEnd)
  const woff2=sources.match(/url\(\s*(?:["'][^"']*["']|[^)]*)\s*\)\s*format\(\s*["']?woff2["']?\s*\)/i)
  return woff2 ? `${css.slice(0,valueStart)} ${woff2[0]}${css.slice(valueEnd)}` : css
}
async function embedCss(css: string, base: string, signal?:AbortSignal) {
  // 导出快照完全离线，主题资源必须来自应用同源地址并在此转换为 data URL。
  // Chromium 支持 WOFF2；丢弃同一字体的 WOFF/TTF 回退，避免重复嵌入三份字体。
  css=preferWoff2FontSource(css)
  const matches=[...css.matchAll(/url\(\s*(['"]?)(.*?)\1\s*\)/g)]
  for(const match of matches) {
    const url=match[2]!
    if(url.startsWith('data:')||url.startsWith('#'))continue
    const absolute=new URL(url,base)
    if(absolute.origin!==location.origin)throw Error(`导出主题资源必须来自应用：${absolute.href}`)
    css=css.replace(match[0],`url("${await dataUrl(absolute.href,signal)}")`)
  }
  return css
}
function stylesheetSnapshot(): {css:string;base:string}[] {
  const sheets: {css:string;base:string}[]=[]
  function visit(sheet:CSSStyleSheet) {
    for(const rule of [...sheet.cssRules]) {
      if(rule instanceof CSSImportRule && rule.styleSheet)visit(rule.styleSheet)
      else sheets.push({css:rule.cssText,base:sheet.href || document.baseURI})
    }
  }
  for(const sheet of [...document.styleSheets])visit(sheet)
  return sheets
}

export async function prepareExportSnapshot(markdown:string,title:string,options:Options,format:SnapshotFormat,signal?:AbortSignal,filePath?:string):Promise<string> {
  // 在任何异步资源请求前冻结主题、排版和编辑器内容，保证产物对应点击导出时的状态。
  signal?.throwIfAborted()
  const theme=useThemeStore(), preferences={...useMarkdownPreferencesStore().normalized}, heading=useHeadingAppearanceStore()
  if(theme.currentThemeId && theme.currentThemeId!==options.theme_id)throw Error('主题在导出准备期间发生变化，请重新导出。')
  const htmlAttrs=attrs(document.documentElement), bodyAttrs=attrs(document.body)
  const variables=getComputedStyle(document.documentElement)
  const rootVariables=[...variables].filter(name=>name.startsWith('--')).map(name=>`${name}:${variables.getPropertyValue(name)};`).join('')
  const styles=stylesheetSnapshot()
  const diagramVariables=mermaidThemeVariables(theme.isDark)
  const headingStyle=Object.entries(heading.cssVariables).map(([key,value])=>`${key}:${value}`).join(';')
  const customHeading=heading.preferences.custom
  const dark=theme.isDark
  const metadata=splitNoteMetadata(markdown)
  const body=metadata?.body ?? markdown
  const scope=scopeAttributes(VisualMarkdownEditor)
  // 复用编辑器 DOM 与 scoped 样式；元数据只输出展示内容，不携带编辑控件。
  const metadataHtml=metadata ? `<section class="note-metadata"${scope} aria-label="${escape(t('笔记属性','Note properties'))}"><span class="metadata-caption"${scope}>${escape(t('笔记属性','Note properties'))}</span>${metadata.title ? `<h1${scope}>${escape(metadata.title)}</h1>` : ''}<div class="metadata-tags"${scope}><span class="metadata-label"${scope}>${escape(t('标签','Tags'))}</span>${metadata.tags.map(tag=>`<span class="metadata-tag"${scope}><span${scope}>${escape(tag)}</span></span>`).join('')}</div></section>` : ''
  // 仅含元数据的笔记没有正文资源，跳过请求可避免空 Markdown 触发接口的 422 校验。
  const resources:Resources=body.trim() ? await apiClient.post<Resources>('/api/exports/preview-resources',{format:'pdf',source:{type:'markdown',markdown:body,file_path:filePath},options}) : {images:[],plots:[]}
  signal?.throwIfAborted()
  const rendered=await renderMarkdown(body,{themeId:options.theme_id,theme:dark?'dark':'light',preferences,pdf:{mermaidVariables:diagramVariables,plot:async source=>{
    const plot=resources.plots.find(p=>p.source.trim()===source.trim()); if(!plot?.svg)throw Error(plot?.warnings.join('; ')||'函数图像无法导出');return plot
  }}})
  const fragment=new DOMParser().parseFromString(rendered,'text/html')
  for(const image of fragment.querySelectorAll('img')) {
    const source=image.getAttribute('src')||''
    if(source.startsWith('data:'))continue
    const resource=resources.images.find(item=>item.source===source)
    if(!resource?.data)throw Error(resource?.warnings.join('; ')||`导出图片无法读取：${source}`)
    image.src=resource.data
  }
  // 打印全部警告框内容，只移除交互控件，保留主题装饰。
  fragment.querySelectorAll('details').forEach(d=>d.open=true)
  // 工作区使用 blockquote 表示警告框；保持相同 DOM 契约，让间距和装饰选择器继续生效。
  fragment.querySelectorAll('.markdown-callout:not(blockquote)').forEach(details=>{
    const block=fragment.createElement('blockquote')
    for(const attribute of [...details.attributes])if(attribute.name!=='open')block.setAttribute(attribute.name,attribute.value)
    block.innerHTML=details.innerHTML
    const summary=block.querySelector('summary')
    if(summary){const title=fragment.createElement('div');title.className=summary.className;title.innerHTML=summary.innerHTML;summary.replaceWith(title)}
    details.replaceWith(block)
  })
  const error=fragment.querySelector('.mermaid-error')
  if(error)throw Error(error.textContent||'导出图表渲染失败')
  fragment.querySelectorAll('.markdown-code-toolbar button,.diagram-controls').forEach(e=>e.remove())
  const css=(await Promise.all(styles.map(s=>embedCss(s.css,s.base,signal)))).join('\n')
  signal?.throwIfAborted()
  const csp="default-src 'none'; script-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src data:; connect-src 'none'; frame-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'"
  const formatRules=format==='pdf'?printRules:''
  const documentClass=format==='pdf'?'visual-editor export-document pdf-document':'visual-editor export-document'
  return `<!doctype html><html${htmlAttrs}><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="${csp}"><meta name="viewport" content="width=device-width, initial-scale=1"><title>${escape(title)}</title><style>${css.replace(/<\/style/gi,'<\\/style')}\n:root{${rootVariables}}\n${formatRules}</style></head><body${bodyAttrs}><div class="${documentClass}"${scope} ${customHeading?'data-heading-style="custom"':''} style="${escape(headingStyle)}"><div class="milkdown-host"${scope}>${metadataHtml}<div class="milkdown"><article class="ProseMirror markdown-content" data-code-wrap="${preferences.wrapCode}" data-line-numbers="${preferences.lineNumbers}" style="--markdown-code-indent:${preferences.indent}">${options.include_title?`<h1>${escape(title)}</h1>`:''}${fragment.body.innerHTML}</article></div></div></div></body></html>`
}

export function preparePdfSnapshot(markdown:string,title:string,options:Options,signal?:AbortSignal,filePath?:string):Promise<string> {
  return prepareExportSnapshot(markdown,title,options,'pdf',signal,filePath)
}
