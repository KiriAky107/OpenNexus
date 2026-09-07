// @vitest-environment jsdom
import {it,expect,vi,afterEach} from 'vitest'
vi.mock('@/utils/markdown',()=>({renderMarkdown:vi.fn().mockResolvedValue('<h1>Heading</h1><details><summary>Tip</summary><p>Body</p></details><div class="markdown-code-toolbar"><button>Copy</button><span>python</span></div>')}))
vi.mock('./apiClient',()=>({apiClient:{post:vi.fn().mockResolvedValue({images:[],plots:[]})}}))
vi.mock('./mermaidService',()=>({mermaidThemeVariables:()=>({primaryColor:'#fff'})}))
vi.mock('@/stores/theme',()=>({useThemeStore:()=>({isDark:false})}))
vi.mock('@/stores/markdownPreferences',()=>({useMarkdownPreferencesStore:()=>({normalized:{wrapCode:true,lineNumbers:true,indent:4}})}))
vi.mock('@/stores/headingAppearance',()=>({useHeadingAppearanceStore:()=>({cssVariables:{'--heading-1-size':'37px'},preferences:{custom:true}})}))
vi.mock('@/components/common/MarkdownContent.vue',()=>({default:{}}))
vi.mock('@/features/editor/VisualMarkdownEditor.vue',()=>({default:{__scopeId:'data-v-editor'}}))
import {preparePdfSnapshot} from './pdfSnapshotService'
import {apiClient} from './apiClient'
import {renderMarkdown} from '@/utils/markdown'
afterEach(()=>vi.clearAllMocks())
it('preserves actual theme CSS, pseudo elements, root attributes and heading preferences',async()=>{
 const style=document.createElement('style');style.textContent='[data-theme="paper"] .ProseMirror::before { content:"tape"; transform:rotate(-3deg) }';document.head.append(style)
 document.documentElement.dataset.theme='paper'
 try {
  const html=await preparePdfSnapshot('# Heading','<Title>',{theme_id:'paper',include_title:true,page_size:'A4'})
  expect(html).toContain('transform: rotate(-3deg)')
  expect(html).toContain('data-theme="paper"')
  expect(html).toContain('data-v-editor')
  expect(html).toContain('data-heading-style="custom"')
  expect(html).toContain('--heading-1-size:37px')
  expect(html).toContain('&lt;Title&gt;')
  expect(html).toContain('<details open="">')
  expect(html).not.toContain('<button>Copy')
  expect(html).toContain('<span>python</span>')
  expect(renderMarkdown).toHaveBeenCalledWith('# Heading',expect.objectContaining({pdf:expect.anything()}))
 } finally {style.remove();delete document.documentElement.dataset.theme}
})
it('rejects a missing image instead of silently producing an incomplete PDF',async()=>{
 vi.mocked(renderMarkdown).mockResolvedValueOnce('<img src="missing.png">')
 await expect(preparePdfSnapshot('![image](missing.png)','note',{theme_id:'light',include_title:false,page_size:'A4'})).rejects.toThrow('PDF 图片无法读取')
})
it('an aborted snapshot never requests backend resources',async()=>{
 const controller=new AbortController();controller.abort()
 await expect(preparePdfSnapshot('text','note',{theme_id:'light',include_title:false,page_size:'A4'},controller.signal)).rejects.toMatchObject({name:'AbortError'})
 expect(apiClient.post).not.toHaveBeenCalled()
})

it('renders snapshot metadata with editor theme scopes and excludes YAML from the body',async()=>{
 const markdown='---\ntitle: "<Current & title>"\ntags: ["<tag>", "未保存标签"]\n---\n# Body'
 const html=await preparePdfSnapshot(markdown,'filename',{theme_id:'light',include_title:false,page_size:'A4'})
 const doc=new DOMParser().parseFromString(html,'text/html')
 const metadata=doc.querySelector('.milkdown-host > .note-metadata')!
 expect(metadata.querySelector('h1')?.textContent).toBe('<Current & title>')
 expect([...metadata.querySelectorAll('.metadata-tag')].map(el=>el.textContent)).toEqual(['<tag>','未保存标签'])
 expect([...metadata.querySelectorAll('*')].every(el=>el.hasAttribute('data-v-editor'))).toBe(true)
 expect(metadata.querySelector('button,input,form')).toBeNull()
 expect(renderMarkdown).toHaveBeenCalledWith('# Body',expect.anything())
 expect(apiClient.post).toHaveBeenCalledWith('/api/exports/preview-resources',expect.objectContaining({source:expect.objectContaining({markdown:'# Body'})}))
})
it('does not invent a metadata bar for plain notes or unsupported frontmatter',async()=>{
 for(const source of ['plain text','---\ntitle: [invalid]\n---\nbody']) {
  const html=await preparePdfSnapshot(source,'filename',{theme_id:'light',include_title:true,page_size:'A4'})
  expect(html).not.toContain('<section class="note-metadata"')
  expect(renderMarkdown).toHaveBeenCalledWith(source,expect.anything())
 }
})

it('exports metadata-only notes without submitting an empty resource request',async()=>{
 for(const tail of ['', '\n  \n']) {
  const html=await preparePdfSnapshot('---\ntitle: Metadata only\ntags: [draft]\n---\n'+tail,'note',{theme_id:'light',include_title:false,page_size:'A4'})
  const doc=new DOMParser().parseFromString(html,'text/html')
  expect(doc.querySelector('.note-metadata h1')?.textContent).toBe('Metadata only')
  expect(doc.querySelector('.metadata-tag')?.textContent).toBe('draft')
 }
 expect(apiClient.post).not.toHaveBeenCalled()
})
it('embeds prepared HTML images without retaining local URLs',async()=>{
 vi.mocked(renderMarkdown).mockResolvedValueOnce('<p><img src="assets/a&amp;b.png"></p>')
 vi.mocked(apiClient.post).mockResolvedValueOnce({images:[{source:'assets/a&b.png',data:'data:image/png;base64,aGVsbG8=',warnings:[]}],plots:[]})
 const html=await preparePdfSnapshot('<img src="assets/a&amp;b.png">','note',{theme_id:'light',include_title:false,page_size:'A4'})
 expect(new DOMParser().parseFromString(html,'text/html').querySelector('img')?.getAttribute('src')).toBe('data:image/png;base64,aGVsbG8=')
})
