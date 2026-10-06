import { expect, it } from 'vitest'
import { syncDiff } from './syncDiff'
it('aligns multiple changes and preserves CRLF, Chinese and literal HTML as data', () => {
  const result = syncDiff('标题\r\n甲\r\n共同\r\n旧\r\n', '标题\r\n乙😀\r\n共同\r\n<img onerror=alert(1)>\r\n')
  expect(result.rows.filter(row => row.kind === 'removed').map(row => row.text)).toEqual(['甲\r', '旧\r'])
  expect(result.rows.filter(row => row.kind === 'added').map(row => row.text)).toEqual(['乙😀\r', '<img onerror=alert(1)>\r'])
  expect(result.truncated).toBe(false)
})
it('bounds the matrix and DOM rows and announces incomplete comparisons', () => {
  const result = syncDiff(Array.from({ length: 5000 }, (_, i) => `old${i}`).join('\n'), Array.from({ length: 5000 }, (_, i) => `new${i}`).join('\n'))
  expect(result.rows.length).toBe(240)
  expect(result.truncated).toBe(true)
  expect(syncDiff('a\r\n', 'a\n').rows.some(row => row.kind !== 'same')).toBe(true)
  expect(syncDiff('same', 'same')).toEqual({ rows: [], truncated: false })
})
