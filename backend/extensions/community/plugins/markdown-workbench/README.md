# Markdown 笔记检查 1.0.0

真实的本地 MCP stdio Plugin，仅依赖 Python 3.11+ 标准库。需要 AI Core 主机能够运行 `python`；当前 NotesAgent 仅在 development 模式允许启动此类本地进程。

## 功能

- Agent 工具 `markdown-workbench.inspect_markdown`：传入 `text`，返回行数、字符数、标题、任务、未完成任务、重复标题、标题跳级及未闭合代码围栏。结果包含 1 起始行号。
- 命令 `检查选中 Markdown`：选择笔记中的文字后，在命令面板（Ctrl+P）执行；通知展示统计和前三条问题。不会修改选区。
- `example.md` 是可独立检查的示例，预期 3 个标题、2 项任务（1 项未完成）、2 条提示（标题跳级、重复标题）。

## 安装

在 Plugin 页面安装 `markdown-workbench-1.0.0.zip`，再启用 Plugin。随后安装并启用配套 Skill `note-reviewer`。本 Plugin 不申请宿主权限，不读取磁盘笔记、不连接网络、不需要密钥；只分析宿主显式传入的文本。宿主本地进程隔离仍不是 OS 沙箱。

## 输入与限制

```json
{"text":"# 周会\n### 计划\n- [ ] 发布社区包\n"}
```

逐行规则支持 ATX、单行 Setext 标题和最多三级空格缩进的任务项，跳过开头已闭合的 YAML frontmatter、围栏代码、缩进代码和引用行。它不是完整 CommonMark AST 解析器，不处理复杂容器嵌套或跨行 Setext 标题，不验证链接可访问性或笔记事实。格式提示由用户决定是否修正。

最多输入 100000 字符，每类详情最多 200 条，统计保持完整，超出列表时 `truncated=true`。检查节选时行号相对于节选。调用失败通过 MCP `isError` 返回，不伪造成功结果。

源码和 ZIP 为社区准备版本，尚未发布远程社区；许可证由仓库维护者确认后补齐。
