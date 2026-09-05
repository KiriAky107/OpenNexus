// @vitest-environment happy-dom
import { mount } from '@vue/test-utils'
import { expect, it } from 'vitest'
import UsageChart from './UsageChart.vue'
it('distinguishes unreported tokens from zero and switches to request counts', async () => {
  const wrapper = mount(UsageChart, { props: { buckets: [{ date: '2026-09-05', end_date: '2026-09-05',
    local: { requests: 1, totals: { input_tokens: null }, coverage: { input_tokens: 0 } },
    api: { requests: 2, totals: { input_tokens: 0 }, coverage: { input_tokens: 1 } },
  }] } })
  expect(wrapper.findAll('.usage-bar.missing')).toHaveLength(1)
  expect(wrapper.get('.usage-bar.local').attributes('aria-label')).toContain('未提供')
  expect(wrapper.get('.usage-bar.api').attributes('aria-label')).toContain('覆盖 1/2')
  await wrapper.get('select').setValue('requests')
  expect(wrapper.findAll('.usage-bar.missing')).toHaveLength(0)
  expect(wrapper.get('.usage-bar.api').attributes('style')).toContain('160px')
  wrapper.unmount()
})
