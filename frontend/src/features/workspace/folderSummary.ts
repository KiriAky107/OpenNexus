import { splitNoteMetadata } from '@/utils/noteMetadata'
export function folderNoteSummary(path: string, content: string) {
  const metadata = splitNoteMetadata(content), body = metadata?.body ?? content
  const title = metadata?.title || body.match(/^#{1,6}\s+(.+)$/m)?.[1]?.replace(/[*_`]/g,'') || path.split('/').at(-1)!.replace(/\.md$/i,'')
  const text = body.slice(0,10000).replace(/```[\s\S]*?```|~~~[\s\S]*?~~~/g,' ').replace(/^#{1,6}\s+.*$/gm,' ').replace(/!\[[^\]]*\]\([^)]*\)/g,' ').replace(/\[([^\]]*)\]\([^)]*\)/g,'$1').replace(/<[^>]*>/g,' ').replace(/[*_`>#]/g,'').replace(/\s+/g,' ').trim()
  return {title, summary:Array.from(text).slice(0,180).join('')+(Array.from(text).length>180?'…':'')}
}
