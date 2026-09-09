import { Marked } from 'marked'
import type { Citation } from '@/contracts'

const parser = new Marked()

/** 候选订单是发送给模型的源编号；永远不要对子集重新编号。 */
export function usedCitations(content: string, candidates: Citation[] = []) {
  const numbers = new Set<number>()
  const aliases = new Map(candidates.map((citation, index) => [citation.citation_id, index + 1]))
  parser.walkTokens(parser.lexer(content), token => {
    // 忽略代码、转义括号、HTML 和链接目标。
    if (token.type !== 'text' || ('tokens' in token && token.tokens?.length)) return
    for (const match of token.text.matchAll(/\[([1-9]\d*|cit_[A-Za-z0-9_-]+)\]/g)) {
      const number = aliases.get(match[1]) ?? Number(match[1])
      if (number > 0 && number <= candidates.length) numbers.add(number)
    }
  })
  return [...numbers].map(number => ({ number, citation: candidates[number - 1]! }))
}
