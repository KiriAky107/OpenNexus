import { describe, expect, it } from 'vitest'
import { parseDocument } from 'yaml'
import { previewPropertyImport } from './importProperties'

describe('笔记属性导入事务预览', () => {
  it('历史格式规范化，保留复杂字段、顺序标签与行为配置', () => {
    const source = '***\ntitle: 中文\ntags: [笔记, "空 格", 笔记]\nembedding_local_only: true\ncustom:\n  nested: [1, false]\n---\n# 正文'
    const result = previewPropertyImport(source).content!
    expect(result.startsWith('---\n')).toBe(true)
    expect(result).toContain('embedding_local_only: true')
    expect(result).toContain('nested: [ 1, false ]')
    expect(result).toContain('# 正文')
    expect(previewPropertyImport(result).content).toBe(result)
  })
  it('字段冲突必须明确选择，未选时无候选内容', () => {
    const source = '---\ntitle: 原标题\n---\n\n***\ntitle: 新标题\ntags: a,b\n---\n正文'
    const from = source.indexOf('***'), to = source.lastIndexOf('正文')
    expect(previewPropertyImport(source, { from, to }).content).toBeNull()
    const result = previewPropertyImport(source, { from, to }, { title: 'incoming' }).content!
    expect(result).toContain('title: 新标题')
    expect(result.match(/^---$/gm)).toHaveLength(2)
  })
  it('普通分隔线、正文和代码不能误判', () => {
    for (const source of ['---\n普通正文\n---', '正文: 值', '***\nother: body\n---']) expect(() => previewPropertyImport(source)).toThrow()
    const source = '```yaml\n---\ntitle: example\n---\n```'
    expect(() => previewPropertyImport(source, { from: 8, to: source.length - 3 })).toThrow('代码块')
  })
  it('单块锚点保持别名语义；非法重复键不改内容', () => {
    const source = '---\ntitle: 标题\ncustom: &value [1, 2]\ncopy: *value\n---\n正文'
    const output = previewPropertyImport(source).content!
    const yaml = output.split('---')[1]!
    const value = parseDocument(yaml).toJS()
    expect(value.copy).toEqual([1, 2])
    expect(() => previewPropertyImport('---\ntitle: a\ntitle: b\n---')).toThrow()
  })
})
