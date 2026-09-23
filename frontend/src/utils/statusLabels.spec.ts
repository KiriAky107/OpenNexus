import { expect, it } from 'vitest'
import { cronSummary, localDateTime, statusLabel } from './statusLabels'

it('describes common schedules without guessing complex cron rules', () => {
  expect(cronSummary('0 9 * * *')).toBe('每天 09:00')
  expect(cronSummary('30 8 * * 1-5')).toBe('工作日 08:30')
  expect(cronSummary('*/7 * * * *')).toBe('每小时内每 7 分钟')
  expect(cronSummary('0 9 1 * *')).toBe('自定义循环')
  expect(cronSummary('0 25 * * *')).toBe('自定义循环')
  expect(cronSummary('invalid')).toBe('自定义循环')
})

it('formats datetime-local in local time and round-trips the instant', () => {
  const value = '2026-09-23T01:30:00Z'
  expect(new Date(localDateTime(value)).getTime()).toBe(new Date(value).getTime())
  expect(localDateTime('bad')).toBe('')
})

it('localizes known states and preserves unknown states for diagnosis', () => {
  expect(statusLabel('done')).toBe('已完成')
  expect(statusLabel('dependency_missing')).toBe('缺少依赖')
  expect(statusLabel('future_state')).toBe('future_state')
})
