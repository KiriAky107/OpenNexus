import { Marked } from 'marked'
import DOMPurify from 'dompurify'
import { resolveVaultReference } from '@/services/vaultReferences'

const escaped = (value: string) => value.replace(/[&<>"']/g, c => ({ '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;' })[c]!)
const parser = new Marked({ async: false, gfm: true })
parser.use({ renderer: {
  html({ text }) { return escaped(text) },
  image({ href, text }) { return `<span class="image-placeholder" data-mobile-image="${encodeURIComponent(href)}" role="img" aria-label="${escaped(text)}">${escaped(text || href)}</span>` },
  link({ href, tokens }) { return `<a href="#" data-mobile-link="${encodeURIComponent(href)}">${this.parser.parseInline(tokens)}</a>` },
} })

export function renderMobileMarkdown(content: string): string {
  const html = parser.parse(content) as string
  return DOMPurify.sanitize(html, {
    ALLOWED_TAGS: ['p','h1','h2','h3','h4','h5','h6','ul','ol','li','blockquote','pre','code','em','strong','del','hr','br','table','thead','tbody','tr','th','td','a','span'],
    ALLOWED_ATTR: ['href','data-mobile-image','data-mobile-link','class','role','aria-label','start','align'],
    ALLOW_DATA_ATTR: false,
  })
}

/** Shared desktop rules reject remote schemes, path escape and private Vault content. */
export function mobileVaultPath(note: string, href: string): string | null {
  return resolveVaultReference(note, href)?.replace(/^\//, '') ?? null
}
