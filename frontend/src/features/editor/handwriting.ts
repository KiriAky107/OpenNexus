import { fromMarkdown } from 'mdast-util-from-markdown'
/** Portable ink in a JSON Canvas text node. Plain text remains the fallback. */
export const INK_KEY = 'opennexusHandwriting'
export const MAX_INK_POINTS = 20000
export interface InkPoint { x: number; y: number; pressure: number }
export interface InkStroke { id: string; color: string; width: number; points: InkPoint[] }
export interface Handwriting {
  version: 1; mode: 'ink' | 'text'; width: number; height: number
  paper: 'plain' | 'ruled' | 'grid'; strokes: InkStroke[]
  fontSize: number; lineHeight: number; spacing: number; variation: number; seed: number
}
const number = (v: unknown, min: number, max: number): v is number => typeof v === 'number' && Number.isFinite(v) && v >= min && v <= max
const object = (v: unknown): v is Record<string, unknown> => !!v && typeof v === 'object' && !Array.isArray(v)
export function validHandwriting(value: unknown): value is Handwriting {
  if (!object(value) || value.version !== 1 || (typeof value.mode !== 'string' || !['ink', 'text'].includes(value.mode))
    || (typeof value.paper !== 'string' || !['plain', 'ruled', 'grid'].includes(value.paper))
    || !number(value.width, 64, 4096) || !number(value.height, 64, 4096)
    || !number(value.fontSize, 12, 72) || !number(value.lineHeight, 1, 3)
    || !number(value.spacing, 0, 12) || !number(value.variation, 0, .3)
    || !number(value.seed, 0, 2147483647) || !Number.isInteger(value.seed)
    || !Array.isArray(value.strokes) || value.strokes.length > 1000) return false
  let count = 0
  const ids = new Set<string>()
  for (const stroke of value.strokes) {
    if (!object(stroke) || typeof stroke.id !== 'string' || !stroke.id || new TextEncoder().encode(stroke.id).length > 128 || ids.has(stroke.id)
      || typeof stroke.color !== 'string' || !/^#[\da-f]{6}$/i.test(stroke.color) || !number(stroke.width, .5, 24)
      || !Array.isArray(stroke.points) || !stroke.points.length || (count += stroke.points.length) > MAX_INK_POINTS) return false
    ids.add(stroke.id)
    if (!stroke.points.every(p => object(p) && number(p.x, 0, value.width as number) && number(p.y, 0, value.height as number) && number(p.pressure, 0, 1))) return false
  }
  return value.mode !== 'text' || value.strokes.length === 0
}
export function handwritingOf(node?: { [key: string]: unknown }): Handwriting | undefined {
  return node && validHandwriting(node[INK_KEY]) ? node[INK_KEY] : undefined
}
export function emptyHandwriting(mode: Handwriting['mode'] = 'ink'): Handwriting {
  return { version: 1, mode, width: 960, height: 640, paper: 'ruled', strokes: [], fontSize: 28, lineHeight: 1.7, spacing: 1, variation: .08, seed: 42 }
}
export const cloneInk = (ink: Handwriting): Handwriting => JSON.parse(JSON.stringify(ink))

/** A pressure ribbon, including single-point dots; no DOM or arbitrary SVG input. */
export function strokePath(stroke: InkStroke): string {
  const p = stroke.points
  const radius = (point: InkPoint) => stroke.width * (.25 + point.pressure * .75) / 2
  const first = p[0]!
  if (p.length === 1) { const r = radius(first); return `M ${first.x-r} ${first.y} a ${r} ${r} 0 1 0 ${r*2} 0 a ${r} ${r} 0 1 0 ${-r*2} 0` }
  const left: string[] = [], right: string[] = []
  p.forEach((point, index) => {
    const before = p[Math.max(0,index-1)]!, after = p[Math.min(p.length-1,index+1)]!
    const dx = after.x-before.x, dy = after.y-before.y, length = Math.hypot(dx,dy) || 1, r = radius(point)
    left.push(`${point.x-dy/length*r} ${point.y+dx/length*r}`)
    right.push(`${point.x+dy/length*r} ${point.y-dx/length*r}`)
  })
  return `M ${left.join(' L ')} L ${right.reverse().join(' L ')} Z`
}
export interface HandwrittenGlyph { char: string; x: number; y: number; rotation: number }
/** Remove Markdown presentation markers through its syntax tree, never arithmetic regexes. */
export function handwritingPlainText(source: string): string {
  type Node={type:string;value?:string;alt?:string;children?:Node[];ordered?:boolean;start?:number}
  const visit=(node:Node,depth=0):string=>{
    if(depth>64)throw Error('HANDWRITING_MARKDOWN_DEPTH')
    if(node.type==='image')return node.alt??''
    if(node.type==='break')return '\n'
    if(typeof node.value==='string')return node.value
    const children=node.children??[]
    if(node.type==='list')return children.map((item,index)=>`${node.ordered?`${(node.start??1)+index}.`:'•'} ${visit(item,depth+1)}`).join('\n')
    const separator=['root','listItem','blockquote'].includes(node.type)?'\n\n':''
    return children.map(child=>visit(child,depth+1)).join(separator)
  }
  try { return visit(fromMarkdown(source) as Node) } catch { return source }
}
/** Panache-inspired spacing/perturbation, generated locally without flattening source text. */
export function layoutHandwrittenText(text: string, ink: Handwriting): { glyphs: HandwrittenGlyph[]; overflow: boolean } {
  let seed = ink.seed >>> 0
  const random = () => { seed = (Math.imul(seed,1664525)+1013904223) >>> 0; return seed/4294967296-.5 }
  const margin = 24, line = ink.fontSize * ink.lineHeight, glyphs: HandwrittenGlyph[] = []
  let x = margin, y = margin + ink.fontSize
  for (const char of Array.from(handwritingPlainText(text).replace(/\r\n?/g,'\n'))) {
    if (char === '\n') { x=margin; y+=line; continue }
    const wide = char.codePointAt(0)! > 255, advance = ink.fontSize * (wide ? 1 : char === ' ' ? .4 : .62) + ink.spacing
    if (x+advance > ink.width-margin) { x=margin; y+=line }
    if (y > ink.height-margin) return { glyphs, overflow: true }
    glyphs.push({char,x:x+random()*ink.fontSize*ink.variation,y:y+random()*ink.fontSize*ink.variation,rotation:random()*ink.variation*20})
    x += advance
  }
  return { glyphs, overflow: false }
}

/** One pointer owns a stroke. Multi-touch, palm contact, cancel and lost capture never commit it. */
export class InkInput {
  private pointers = new Set<number>()
  private active?: { id: number; type: string; stroke: InkStroke }
  overflowed=false
  get draft() { return this.active?.stroke }
  down(id: number, type: string, point: InkPoint, color: string, width: number, penOnly: boolean) {
    if ((penOnly && type !== 'pen') || (this.active?.type === 'pen' && type !== 'pen')) return
    if (type === 'pen' && this.active?.type !== 'pen') { this.active=undefined; this.pointers.clear() }
    if (this.pointers.has(id)) return
    this.pointers.add(id)
    if (this.pointers.size !== 1) { this.active=undefined; return }
    this.overflowed=false
    this.active={id,type,stroke:{id:crypto.randomUUID(),color,width,points:[{...point}]}}
  }
  move(id: number, points: InkPoint[]) {
    if (this.active?.id !== id) return
    for (const point of points) {
      const last = this.active.stroke.points.at(-1)!
      if (Math.hypot(point.x-last.x,point.y-last.y) < .5) continue
      if (this.active.stroke.points.length >= MAX_INK_POINTS) {this.overflowed=true;continue}
      this.active.stroke.points.push({...point})
    }
  }
  up(id: number): InkStroke | undefined {
    const stroke = this.active?.id === id && !this.overflowed ? this.active.stroke : undefined
    this.pointers.delete(id); if (this.active?.id === id) this.active=undefined
    return stroke
  }
  cancel(id?: number) { if (id === undefined) {this.pointers.clear();this.active=undefined} else {this.pointers.delete(id);if(this.active?.id===id)this.active=undefined} }
}

export function eraseStroke(ink: Handwriting, point: InkPoint, radius = 14): boolean {
  const distance = (a: InkPoint, b: InkPoint) => {
    const dx=b.x-a.x,dy=b.y-a.y, t=Math.max(0,Math.min(1,((point.x-a.x)*dx+(point.y-a.y)*dy)/(dx*dx+dy*dy||1)))
    return Math.hypot(point.x-a.x-t*dx,point.y-a.y-t*dy)
  }
  for (let i=ink.strokes.length-1;i>=0;i--) {
    const stroke=ink.strokes[i]!
    if (stroke.points.some((p,j)=>distance(p,stroke.points[Math.max(0,j-1)]!)<=radius+stroke.width/2)) { ink.strokes.splice(i,1); return true }
  }
  return false
}
