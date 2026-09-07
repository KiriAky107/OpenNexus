// @vitest-environment jsdom
import { describe,it,expect,vi,beforeEach } from 'vitest'
vi.mock('./apiClient',()=>({apiClient:{post:vi.fn()}}))
import { apiClient } from './apiClient'
import { renderFunctionPlot } from './functionPlotService'
describe('function plot preview boundary',()=>{
  beforeEach(()=>vi.clearAllMocks())
  it('shares requests only for the same source and theme, sanitizes SVG',async()=>{
    vi.mocked(apiClient.post).mockResolvedValue({result:{content:'<svg onload="alert(1)"><script>alert(2)</script><path d="M0 0L1 1"/></svg>',warnings:[]},diagnostics:[],node_count:3})
    const a=await renderFunctionPlot('y = x + 100','dark')
    await renderFunctionPlot('y = x + 100','dark')
    await renderFunctionPlot('y = x + 100','light')
    expect(apiClient.post).toHaveBeenCalledTimes(2)
    expect(a.svg).not.toMatch(/onload|script|alert/)
  })
  it('does not cache network failures',async()=>{
    vi.mocked(apiClient.post).mockRejectedValueOnce(new Error('offline')).mockResolvedValueOnce({result:null,diagnostics:[{message:'bad expression',line:2}],node_count:0})
    await expect(renderFunctionPlot('bad expression')).rejects.toThrow('offline')
    const result=await renderFunctionPlot('bad expression')
    expect(result.svg).toBe('');expect(result.warnings[0]).toContain('2')
  })
})
