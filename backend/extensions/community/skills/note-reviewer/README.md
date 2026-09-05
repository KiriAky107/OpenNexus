# 笔记检查助手 1.0.0

配套 `markdown-workbench` Plugin 的只读 Skill。根据用户指定的笔记，搜索、读取完整原文，再调用本地分析工具给出带行号的格式提示与待办清单。提示词位于 `prompt.md`，可审阅、修改后重新打包。

安装顺序：安装并启用 Plugin `markdown-workbench` → 安装并启用本 Skill → 在智能体页面选择“笔记检查助手”和支持 chat/tool_calling 的 Provider。

示例请求：`检查我的周会记录，列出标题问题和未完成任务，不要修改笔记。`

权限为 `notes.search`、`notes.read`，不声明写入权限。Skill 的自然语言执行需要模型；选用远程 Provider 时，所选笔记会进入模型上下文，使用本地 Plugin 并不意味着整个 Agent 流程离线。直接执行 Plugin 的选区检查则不需要模型。

清单依赖 `markdown-workbench.inspect_markdown`。未启用对应 Plugin 时宿主会显示缺失依赖；不声称已完成检查。工具规则与限制见 Plugin README。当前验证覆盖真实 ZIP 安装、进程、工具、命令和 Skill 依赖解析；模型生成质量另需专项验收。

源码和 ZIP 为社区准备版本，尚未发布远程社区；许可证由仓库维护者确认后补齐。
