import { Marked } from 'marked'
import type { Citation } from '@/contracts'

const parser = new Marked()

/** Candidate order is the source number sent to the model; never renumber a subset. */
export function usedCitations(content: string, candidates: Citation[] = []) {
  const numbers = new Set<number>()
  const aliases = new Map(candidates.map((citation, index) => [citation.citation_id, index + 1]))
  parser.walkTokens(parser.lexer(content), token => {
    // Ignore code, escaped brackets, HTML and link destinations.
    if (token.type !== 'text' || ('tokens' in token && token.tokens?.length)) return
    for (const match of token.text.matchAll(/\[([1-9]\d*|cit_[A-Za-z0-9_-]+)\]/g)) {
      const number = aliases.get(match[1]) ?? Number(match[1])
      if (number > 0 && number <= candidates.length) numbers.add(number)
    }
  })
  return [...numbers].map(number => ({ number, citation: candidates[number - 1]! }))
}
