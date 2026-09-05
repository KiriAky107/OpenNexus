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
  expect(wrapper.get('.usage-pie').attributes('aria-label')).toContain('本地模型: 0')
  await wrapper.get('.chart-column').trigger('mouseenter')
  expect(wrapper.get('.chart-column').classes()).toContain('highlighted')
  expect(wrapper.get('.chart-readout').text()).toContain('未提供')
  expect(wrapper.get('.usage-bar.local').attributes('aria-label')).toContain('未提供')
  expect(wrapper.get('.usage-bar.api').attributes('aria-label')).toContain('覆盖 1/2')
  await wrapper.get('select').setValue('requests')
  expect(wrapper.findAll('.usage-bar.missing')).toHaveLength(0)
  expect(wrapper.get('.usage-bar.api').attributes('style')).toContain('160px')
  wrapper.unmount()
})


it('stacks models inside the source column and keeps their shades distinct', () => {
  const models = [100, 200].map((count, index) => ({ key: `m${index}`, provider_id: 'p', model: `model-${index}`, requests: 1, totals: { input_tokens: count }, coverage: { input_tokens: 1 } }))
  const wrapper = mount(UsageChart, { props: { buckets: [{ date: '2026-09-05', end_date: '2026-09-05', local: { requests: 0, totals: {}, coverage: {} }, api: { requests: 2, totals: { input_tokens: 300 }, coverage: { input_tokens: 2 }, models } }] } })
  const segments = wrapper.findAll('.model-segment')
  expect(segments).toHaveLength(2)
  expect(segments[0]!.attributes('style')).not.toBe(segments[1]!.attributes('style'))
  expect(wrapper.get('.model-legend').text()).toContain('model-1')
  expect(segments[0]!.attributes('title')).toContain('100')
  wrapper.unmount()
})
