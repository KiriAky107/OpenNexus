# 参与 OpenNexus 开发

**简体中文** | [English](CONTRIBUTING.md)

感谢你改进 OpenNexus。本指南是 [README.zh-CN.md](README.zh-CN.md) 中工程、测试、Issue 和 Pull Request 要求的补充。

## 开始前

1. 搜索已有 Issue 和 Pull Request。
2. 行为变化、架构工作、数据库迁移或跨仓库兼容性变更需要创建或关联 Issue。
3. 确认改动属于本仓库，而不是独立的 Sync Server 或 Community 仓库。
4. 不得将个人 Vault、生产凭据、私人服务器或能识别参赛身份的材料作为测试夹具提交。

## 开发流程

1. Fork 仓库，或从最新 `main` 创建功能分支。
2. 使用仓库锁定文件安装依赖。
3. 只完成一个聚焦的逻辑改动，并增加或更新测试。
4. 运行所有受影响层级的检查。
5. 公共行为变化时同步更新两种 README 和发行说明。
6. 推送功能分支；需要早期方案反馈时可创建 Draft Pull Request。

建议的分支名称：

```text
feat/agent-resume
fix/pdf-export-dialog
docs/community-standards
```

## 提交信息

使用 Conventional Commits：

```text
<类型>(<可选范围>): <祈使句摘要>
```

常用类型包括 `feat`、`fix`、`docs`、`test`、`refactor`、`perf`、`build`、`ci` 和 `chore`。署名必须准确；未经明确请求不得改写其他贡献者的身份。

## 工程要求

- 保持本地优先模型，以及 WebView、Tauri 宿主、AI Core、Vault 和可选远程服务之间的信任边界。
- 高权限桌面文件和凭据操作以 Rust 宿主为权威实现。
- 持久化结构只能通过追加迁移更新，并记录恢复或回滚行为。
- API 保持类型化，接口变化同步更新前端契约、后端 Schema、原生命令和测试。
- 文件、媒体、同步和扩展事务必须保持幂等。
- 校验不可信压缩包、Markdown、扩展清单、路径、URL、模型输出和远程响应。
- 说明新增依赖，并检查其许可证和打包体积。

## 必须执行的检查

使用 [README.zh-CN.md](README.zh-CN.md#测试) 中的命令。Pull Request 只列出实际运行过的命令，并说明无法提供的环境或跳过的检查。

纯文档改动至少应通过 Markdown 链接、代码围栏和 Mermaid 解析检查。涉及界面、原生对话框、Sidecar、导出、安装包或同步的改动，应执行适用的桌面安装版验收。

## 文档与隐私

- 英文与简体中文公共文档保持一致。
- 示例只能使用虚构的用户、域名、Token、路径和 Vault 内容。
- 删除凭据、个人信息、私人主机名、学校或单位信息以及能识别参赛身份的材料。
- 不得提交生成安装包、模型权重、用户数据库、媒体录音或个人文档。

## 审查

审查内容包括正确性、安全边界、迁移安全、测试、可访问性、文档、许可证和发行影响。审查期间应使用新提交响应意见；维护者可以在合并时压缩提交。

参与社区须遵守[社区行为准则](CODE_OF_CONDUCT.zh-CN.md)，安全问题按照 [SECURITY.md](SECURITY.md) 报告。
