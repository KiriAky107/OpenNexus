/** GitHub alerts and Obsidian callouts share the same portable Markdown syntax. */
export const calloutTypes = {
  note: ['note'], abstract: ['abstract', 'summary', 'tldr'], info: ['info'],
  todo: ['todo'], tip: ['tip', 'hint'], important: ['important'], success: ['success', 'check', 'done'],
  question: ['question', 'help', 'faq'], warning: ['warning', 'caution', 'attention'],
  failure: ['failure', 'fail', 'missing'], danger: ['danger', 'error'], bug: ['bug'],
  example: ['example'], quote: ['quote', 'cite'],
} as const

export function parseCallout(text: string) {
  const match = /^\[!([\w-]+)\]([+-]?)[ \t]*([^\n]*)(?:\n|$)/.exec(text)
  if (!match) return null
  const name = match[1]!.toLowerCase()
  const type = Object.entries(calloutTypes).find(([, aliases]) => (aliases as readonly string[]).includes(name))?.[0] ?? 'note'
  return { name, type, title: match[3]!.trim() || name.charAt(0).toUpperCase() + name.slice(1),
    fold: match[2] || null, markerLength: match[0].replace(/\n$/, '').length, body: text.slice(match[0].length) }
}

export function escapeCalloutTitle(text: string) {
  return text.replace(/[&<>"']/g, character => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[character]!)
}
