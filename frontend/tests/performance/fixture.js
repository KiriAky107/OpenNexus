/** 确定性 CJK 散文加上标题、表格、代码和标注；没有用户文档。 */
export function makeStressDocument(minHan = 25000) {
  const prose = '本地知识库保存课程记录与项目思考，编辑时需要稳定响应。长篇文档包含章节结构和引用信息，阅读过程中可以随时折叠展开。这里使用生成的测试内容验证渲染性能，不读取真实笔记。'
  let source = '# 长文渲染压力测试\n\n', han = 0, section = 0
  while (han < minHan) {
    source += `## 第 ${++section} 节：知识整理\n\n${prose.repeat(3)}\n\n### 小结 ${section}\n\n重点包含 **强调文字**、\`inlineCode\` 和 [链接](https://example.com)。\n\n`
    han += prose.length * 3
    if (section % 8 === 0) source += '> [!TIP] 验收提示\n> 内容需要保留，折叠后仍可展开。\n\n| 项目 | 状态 |\n| --- | --- |\n| 渲染 | 待验证 |\n\n```javascript\nconst note = { title: "长文测试", ready: true };\nconsole.log(note);\n```\n\n'
  }
  source += '\n## 文末校验\n\n结束标记：长文内容完整。\n'
  return source
}
