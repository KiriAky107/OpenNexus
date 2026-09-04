// @vitest-environment happy-dom
import { flushPromises, mount } from '@vue/test-utils'
import { expect, it, vi } from 'vitest'
import { apiClient } from '@/services/apiClient'
import UsageCard from './UsageCard.vue'

vi.mock('@/services/apiClient', () => ({apiClient:{get:vi.fn()}}))
it('shows reported zero separately from missing counters and renders coverage', async () => {
  vi.mocked(apiClient.get).mockResolvedValue({totals:{input_tokens:0,output_tokens:12,total_tokens:12,cache_hit_tokens:null,cache_miss_tokens:null,cache_write_tokens:null,reasoning_tokens:null},
    coverage:{input_tokens:1,output_tokens:1,total_tokens:1,cache_hit_tokens:0,cache_miss_tokens:0,cache_write_tokens:0,reasoning_tokens:0},
    request_count:2,complete_requests:1,cache_hit_rate:null,cache_covered_requests:0,options:[]})
  const wrapper = mount(UsageCard)
  await flushPromises()
  expect(wrapper.findAll('.usage-grid strong').map(node => node.text())).toEqual(['0','12','12','未提供','未提供','未提供','未提供','未提供'])
  expect(wrapper.text()).toContain('覆盖 1 / 2 次')
  expect(wrapper.text()).toContain('不是厂商账户账单')
  wrapper.unmount()
})
