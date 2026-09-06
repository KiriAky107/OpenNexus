# NotesAgent Frontend

NotesAgent Frontend 是基于 Vue 3、TypeScript、Vite、Pinia、Vue Router、Milkdown 和 CodeMirror 6 的 Web 联调前端。当前页面调用 FastAPI 真实接口，不使用业务 Mock 作为运行时回退；测试文件中的 mock 只用于隔离单元和组件测试。

## 初始化与运行

```powershell
pnpm install
pnpm dev
```

开发地址为 <http://127.0.0.1:5173>。Vite 将 `/api` 和 `/health` 转发到 <http://127.0.0.1:8000>，因此联调前需要先启动后端。

## 页面与能力

| 路由 | 当前能力 |
| --- | --- |
| `/`、`/workspace` | 选择当前 Vault、浏览目录、编辑和保存 Markdown |
| `/search` | 全文、向量和混合检索；从后端读取并清空搜索历史 |
| `/chat` | 流式 AI 对话、知识库上下文与 Citation |
| `/agent/runs/:runId?` | 创建 Agent 运行，查看可恢复 Trace 与 Tool/Permission 事件 |
| `/media` | 上传音频、创建/取消/重试转写、修订结果并生成知识库笔记 |
| `/tasks` | 管理用户、笔记和 Agent 产生的任务 |
| `/extensions/skills` | Skill 安装、启停与配置 |
| `/extensions/mcp` | stdio、Streamable HTTP、旧 SSE Server 配置与工具发现 |
| `/extensions/plugins` | Plugin Host、Command、Settings、Secret 与 MCP 状态 |
| `/themes` | 内置 Design Token 主题和编辑器显示偏好 |
| `/settings` | Provider、模型路由、本地模型、CPU/CUDA 组件、请求 JSON、用量与诊断；全局中英文和拼写检查设置 |

## 技术结构

| 目录 | 职责 |
| --- | --- |
| `src/features` | 按页面和业务域组织的 Vue 组件 |
| `src/stores` | Pinia 状态与页面编排 |
| `src/services` | FastAPI HTTP/SSE 客户端和 DTO 转换 |
| `src/contracts` | 与后端契约对应的 TypeScript 类型 |
| `src/components` | 应用壳、命令面板和共享组件 |
| `src/utils` | Markdown 清洗、Shiki 高亮等纯工具 |
| `src/styles` | Design Token、布局、主题和动效 |

编辑器使用 Milkdown/Crepe 与 CodeMirror 6；Markdown 展示使用 marked、DOMPurify 和 Shiki。Provider logo 位于 `src/assets/providers`，授权与来源说明随目录保存。

工作区和静态预览支持 GitHub alerts / Obsidian callout 的类型、别名、标题、嵌套与折叠。桌面快捷键使用预留的 v1 编辑命令边界，尚未接入 Tauri 原生快捷键与元数据转换处理器；见 [警告框与桌面编辑命令开发说明](../docs/development/警告框与桌面编辑命令开发说明.md)。

语言设置会即时更新主导航、页面标题和各功能页面，并同步更新文档与编辑器的 `lang`。拼写检查使用浏览器或桌面 WebView 提供的本地词典，开关会即时作用于可视化 Markdown、源码编辑器以及普通文本输入；JSON、密码等结构化或敏感输入保持关闭。

## 数据边界

- 笔记、附件、搜索历史、任务、Trace、模型配置和多模态结果都通过 FastAPI 读写。
- AI 对话生成和知识库检索通过 FastAPI；会话列表、用户消息、流式助手结果、引用和 Token 用量保存在后端 SQLite，刷新页面后可恢复。
- API Key 只存在于密码输入和提交请求期间，不进入 Pinia 或 `localStorage`。
- 页面内存可以保存尚未提交的临时状态；后端已经接收的任务和结果由 SQLite/Vault 持久化。
- 主题、编辑器偏好、侧栏状态和最近 Vault 路径目前保存在浏览器 `localStorage`；它们是设备界面偏好，不作为笔记或模型业务数据。Tauri 集成时由桌面配置存储接管。
- 前端不直接访问 SQLite，不拼装第三方模型协议；Provider Adapter 和请求覆盖规则由后端执行。
- 后端不可用时页面显示连接或操作错误，不生成演示数据替代真实结果。

当前仍运行在 Web/Vite 环境。后续 Tauri 集成将复用现有 Service/Contract 边界，并由 Rust Host 接管窗口、Vault 选择、Sidecar、Stronghold 和生产沙箱。

## 模型设置

设置页支持带 logo 的提供商预设、模型发现、聊天/Embedding/转写/声纹能力绑定，以及按 capability、model 和 stream 条件匹配的自定义请求 JSON。请求预览不联网；“发送测试推理请求”使用当前草稿和已保存凭据执行真实短请求。

本地模型页显示固定 revision、许可、下载状态和实际磁盘占用。CPU 是默认运行方式；Windows 可从页面安装独立 CUDA 12.8 组件，安装过程不修改显卡驱动。当前模型选型详见[多模态管线与模型运行](../docs/development/多模态管线与模型运行开发说明.md)。

## 测试与构建

```powershell
pnpm test
pnpm type-check
pnpm build
```

当前基线为 30 个测试文件、106 项测试通过，TypeScript 类型检查与 Vite 生产构建通过；构建仍有既有大 bundle 提示。产物位于 `dist`，不提交 Git。

## 开发约定

- 依赖统一使用 pnpm 管理，不混用 npm 或 yarn。
- 新接口先更新 `src/contracts` 与 `src/services`，页面和 Store 不直接散落 `fetch` 协议细节。
- 异步页面需要处理加载、空数据、后端错误、重复提交和迟到响应。
- 功能行为或契约变化时，同一提交同步更新测试和相关文档。
- 页面需求见[前端页面需求说明](../docs/contracts/前端页面需求说明-开发版.md)，后端行为以运行时 `/openapi.json` 为准。

## 保存状态与图表联调（2026-09-06）

“已保存”表示正文与全文索引请求成功；向量可能仍在后台计算。状态栏定期刷新索引状态；保存期间继续输入会补存。Vault 入口具有超时、错误与重试提示。

Mermaid 大图打开时适配窗口，支持平滑滚轮缩放和鼠标位置补偿；行内中键启用滚轮控制，移动鼠标退出。标签段落样式与正文隔离，避免 foreignObject 内文字裁切。

开发和验证方法见 [后台索引与保存](../docs/development/工作区后台索引与保存开发说明.md)、[Mermaid 预览与缩放](../docs/development/Mermaid预览与缩放开发说明.md)。

## 构建体积检查

执行 `pnpm build` 后运行 `pnpm build:report`，查看入口静态 JS 依赖与大块清单。分组策略、统计口径及保留的大资源见 [前端构建分块优化开发说明](../docs/development/前端构建分块优化开发说明.md)。
