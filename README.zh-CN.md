# OpenNexus

**简体中文** | [English](README.md)

[![版本](https://img.shields.io/badge/version-0.5.2--alpha1-5865f2)](https://github.com/KiriAky107/OpenNexus/releases/tag/v0.5.2-alpha1)
![平台](https://img.shields.io/badge/platform-Windows%20x64-2563eb)
![桌面端](https://img.shields.io/badge/desktop-Tauri%202-f97316)
![前端](https://img.shields.io/badge/frontend-Vue%203-42b883)
![核心服务](https://img.shields.io/badge/core-FastAPI-05998b)
[![许可证](https://img.shields.io/badge/license-MIT-22c55e)](LICENSE)

OpenNexus 是一款本地优先的 AI 笔记与知识工作台，将 Markdown 知识库、混合检索、知识库问答、可审计智能体、音视频转笔记、扩展运行时和可选多设备同步整合在 Windows 桌面应用中。

> 当前版本为 **0.5.2-alpha1**。本版本可用于评估和功能演示，但存储结构与扩展接口仍可能调整。升级前请备份重要知识库。

## 目录

- [项目定位](#项目定位)
- [核心工作流](#核心工作流)
- [系统架构](#系统架构)
- [数据与持久化模型](#数据与持久化模型)
- [仓库边界](#仓库边界)
- [安装](#安装)
- [开发环境](#开发环境)
- [配置与数据](#配置与数据)
- [测试](#测试)
- [打包与发布](#打包与发布)
- [安全与隐私](#安全与隐私)
- [社区规范](#社区规范)
- [参与开发](#参与开发)
- [许可证](#许可证)
- [Issue 要求](#issue-要求)
- [Pull Request 要求](#pull-request-要求)

## 项目定位

OpenNexus 将笔记视为用户拥有的可移植文件，而不是锁定在云端服务中的记录。桌面宿主负责本机高权限操作，AI Core 通过受限的本机接口提供检索、生成、智能体、转写和导出能力。所有远程服务均为可选组件，并可独立部署。

项目设计重点包括：

- **本地所有权**：笔记保持为普通 Markdown 文件，附件使用可迁移的相对路径与内容寻址方式。
- **可追踪 AI**：检索上下文、工具权限、任务状态和 Agent 执行记录均可检查。
- **可替换模型**：可选择本地模型或多个远程提供商，不将知识库绑定到单一厂商。
- **可组合扩展**：Skill、Plugin、主题和 MCP 服务通过清单、权限和信任审查接入。

## 核心工作流

### 课程录音转知识点笔记

导入真实音频或视频，生成带时间戳的转录片段，完成校对后再生成独立的知识点笔记。内容需要时，笔记可包含代码块、数学公式、Mermaid 图和函数图像。转录与笔记生成分为两个阶段，确保原始转录稿始终可核对。

```mermaid
sequenceDiagram
    autonumber
    actor User as 用户
    participant UI as 音视频界面
    participant Host as Tauri 宿主
    participant Core as AI Core
    participant ASR as 转写服务
    participant Model as 笔记模型
    participant Vault as Markdown Vault
    participant DB as app.db

    User->>UI: 选择音频或视频
    UI->>Host: 打开原生文件选择器
    Host->>Core: 注册附件并创建任务
    Core->>DB: 持久化 media_jobs 和 media_events
    Core->>ASR: 请求带时间戳和说话人的转写
    ASR-->>Core: 返回分段和识别文本
    Core->>DB: 保存完成状态与修订版本
    Core-->>UI: 推送可重放的进度事件
    User->>UI: 校对并修正转录稿
    UI->>Core: 以乐观锁保存修订
    Core->>DB: 追加 media_revisions
    User->>UI: 生成转录稿和知识点笔记
    Core->>Model: 提取有转录依据的课程知识
    Model-->>Core: 返回含可选代码或图表的 Markdown
    Core->>Core: 校验 Mermaid 和 function-plot 块
    Core->>Host: 创建两个幂等笔记产物
    Host->>Vault: 原子写入 Markdown 文件
    Core->>DB: 将 media_notes 关联到笔记 ID
    Core-->>UI: 返回转录稿与知识点笔记
```

### 个人规划智能体

创建目标导向的 Agent，由其生成计划和可执行任务，仅授权必要工具，并检查完整执行历史。任务状态会持久化，异常中断后可以诊断和恢复，而不是静默丢失。

```mermaid
stateDiagram-v2
    [*] --> queued: 创建运行
    queued --> running: 工作进程启动
    running --> waiting_permission: 高权限工具需要审批
    waiting_permission --> running: 用户授权
    waiting_permission --> cancelled: 用户拒绝或取消
    running --> completed: 最终结果已持久化
    running --> failed: 模型、工具或超时错误
    running --> cancelled: 收到取消请求
    queued --> cancelled: 启动前取消
    completed --> [*]
    failed --> [*]
    cancelled --> [*]
```

每次状态变化均对应持久化的运行快照和有序 `agent_events`，因此界面重连时不会把 SSE 中断误判为任务丢失。

### 知识工作台

- 使用源码模式或所见即所得模式编辑 Markdown。
- 通过 SQLite FTS5 和向量检索建立索引。
- 使用 RRF 融合与可选精排合并检索结果。
- 针对当前知识库进行有依据的问答。
- 将编辑器快照导出为 PDF、HTML 或 DOCX。
- 将 Markdown 和演示文稿材料整理为可复用笔记。

### 扩展与可选服务

- 在演示时安装 Skill 和 Plugin，不向用户知识库预装扩展包。
- 通过受支持的传输方式连接 MCP；适用时，MCP 进程生命周期与 AI Core 分离管理。
- 安装社区主题和扩展前检查来源、清单与权限。
- 通过独立部署的 Sync Server 同步笔记和附件。

```mermaid
flowchart LR
    A[本地扩展包或社区 URL] --> B[暂存压缩包]
    B --> C{压缩包、清单、哈希和签名者有效？}
    C -- 否 --> X[拒绝并记录原因]
    C -- 是 --> D[准备隔离的包目录]
    D --> E[计算变更和所需权限]
    E --> F{用户确认完全一致的审查指纹？}
    F -- 否 --> Y[取消且不激活]
    F -- 是 --> G[创建扩展事务]
    G --> H[原子切换活动槽位]
    H --> I{切换后检查通过？}
    I -- 是 --> J[提交回执和活动版本]
    I -- 否 --> K[回滚到先前状态]
```

## 系统架构

```mermaid
flowchart LR
    UI[Vue 3 桌面界面] --> HOST[Tauri 2 / Rust 宿主]
    HOST --> VAULT[本地 Markdown Vault]
    HOST --> CORE[FastAPI AI Core]
    CORE --> INDEX[(SQLite / FTS5 / sqlite-vec)]
    CORE --> MODEL[本地或远程模型]
    CORE --> EXT[Skill / Plugin / MCP]
    HOST <--> SYNC[可选 Sync Server]
    SYNC --> DB[(PostgreSQL)]
    SYNC --> OBJ[S3 兼容对象存储]
    COMMUNITY[社区原型] --> EXT
```

| 层级 | 职责 | 信任边界 |
| --- | --- | --- |
| Vue 前端 | 工作区、编辑器、搜索、对话、Agent、音视频、扩展和设置界面 | 不直接持久化凭据 |
| Tauri/Rust 宿主 | 文件访问、原生对话框、进程监督、凭据保险库和高权限命令 | 桌面安全边界 |
| FastAPI AI Core | 检索、模型适配器、Agent、转写、索引和导出编排 | 经过认证的本机通道 |
| Vault | Markdown 笔记、附件和本地元数据 | 用户选择的目录 |
| 可选服务 | 同步与社区分发 | 独立仓库和发布周期 |

AI Core 由桌面宿主监督运行，但不负责拥有无关 MCP 进程。生产行为不得依赖仅开发环境可用的 `stdio` 假设。

### 桌面启动与认证本机通道

```mermaid
sequenceDiagram
    autonumber
    participant UI as Vue WebView
    participant Host as Tauri 宿主
    participant Core as AI Core Sidecar
    participant Cred as 凭据保险库
    participant Vault as 当前 Vault

    Host->>Host: 获取单实例锁与状态锁
    Host->>Core: 启动版本匹配的打包 Sidecar
    Core-->>Host: 绑定回环地址并报告健康状态
    Host->>Core: 建立经过认证的本机会话
    Host->>Cred: 为当前会话自动解锁模型凭据
    Host->>Vault: 校验所选根目录和宿主数据库
    Host-->>UI: 暴露受限的 Tauri 命令集合
    UI->>Host: 请求工作区或 AI 操作
    Host->>Core: 转发已授权请求
    Core-->>Host: 返回结构化结果或可重放事件
    Host-->>UI: 返回清理后的响应
```

WebView 无法取得模型明文密钥或不受限文件访问能力。原生对话框、Vault 路径校验和 Sidecar 版本匹配均由宿主负责。

## 数据与持久化模型

OpenNexus 有意分离用户创作内容、可重建索引和宿主事务状态。以下存储互有关联，但不是同一个共享数据库：

```mermaid
flowchart TB
    subgraph UserData[用户选择的 Vault]
        MD[Markdown 笔记]
        ATT[内容寻址附件]
    end

    subgraph HostState[Tauri 管理状态]
        HDB[(host.sqlite3)]
        EDB[(extensions.sqlite3)]
        HDB --> FILES[文件身份、日志与 Outbox]
        HDB --> SYNCSTATE[绑定、Head、Inbox 与冲突]
        EDB --> EXTSTATE[版本、信任与安装事务]
    end

    subgraph CoreState[AI Core 状态]
        ADB[(app.db)]
        ADB --> SEARCH[笔记、Block、FTS 与向量]
        ADB --> ACTIVITY[任务、Agent、媒体与对话]
    end

    MD -->|可重建索引投影| SEARCH
    ATT -->|元数据与引用| ADB
    HDB -->|经过授权的宿主桥接| ADB
    EDB -->|活动扩展清单| ADB
```

### AI Core 数据库关系

下图展示 `app.db` 中主要迁移表。FTS 与向量表是 `blocks` 的检索投影；由于桌面 Markdown 的权威写入方是 Rust 宿主，`media_notes.note_id` 是跨边界逻辑引用，而不是对 `notes` 的强外键。

```mermaid
erDiagram
    NOTES ||--o{ BLOCKS : 包含
    BLOCKS ||--o| BLOCKS_FTS : 投影
    BLOCKS ||--o{ ROUTED_VECTORS : 嵌入
    NOTES o|--o{ TASKS : 可选关联
    AGENT_RUNS ||--o{ AGENT_EVENTS : 产生
    MEDIA_JOBS ||--o{ MEDIA_EVENTS : 产生
    MEDIA_JOBS ||--o{ MEDIA_REVISIONS : 保存修订
    MEDIA_JOBS ||--o{ MEDIA_NOTES : 生成
    CHAT_CONVERSATIONS ||--o{ CHAT_MESSAGES : 包含
    CHAT_MESSAGES o|--o{ CHAT_MESSAGES : 消息分支
    WORKSPACE_ASSETS ||--o{ WORKSPACE_ASSET_LINKS : 被引用
    NOTES o|--o{ WORKSPACE_ASSET_LINKS : 使用

    NOTES {
        string note_id PK
        string title
        string file_path UK
        string folder
        json tags
        datetime updated_at
    }
    BLOCKS {
        string block_id PK
        string note_id FK
        json heading_path
        int start_offset
        int end_offset
        string content_hash
        int position
        bool embedding_local_only
    }
    BLOCKS_FTS {
        string block_id
        string note_id
        string heading_path
        string content
    }
    ROUTED_VECTORS {
        string space_id PK
        string block_id PK, FK
        int dimensions PK
        json vector
    }
    TASKS {
        string task_id PK
        string note_id FK
        string status
        datetime due_at
        datetime updated_at
    }
    AGENT_RUNS {
        string run_id PK
        string status
        json run_json
        json request_json
        json config_snapshot_json
        datetime updated_at
    }
    AGENT_EVENTS {
        string run_id PK, FK
        int sequence PK
        string event
        json data_json
        datetime timestamp
    }
    MEDIA_JOBS {
        string job_id PK
        string status
        json job_json
        json request_json
        string idempotency_key UK
        string fingerprint
    }
    MEDIA_EVENTS {
        string job_id PK, FK
        int sequence PK
        string event
        json data_json
    }
    MEDIA_REVISIONS {
        string job_id PK, FK
        int revision PK
        json job_json
    }
    MEDIA_NOTES {
        string job_id PK, FK
        int revision PK
        string options_hash PK
        string note_id
    }
    CHAT_CONVERSATIONS {
        string conversation_id PK
        string title
        string active_leaf
        string active_response_id
        datetime updated_at
    }
    CHAT_MESSAGES {
        string message_id PK
        string conversation_id FK
        string parent_message_id
        int sequence
        string role
        string content
        json citations_json
        json tool_calls_json
    }
    WORKSPACE_ASSETS {
        string asset_id PK
        string path UK
        string content_hash UK
        string media_type
        int size
    }
    WORKSPACE_ASSET_LINKS {
        string asset_id PK, FK
        string note_id PK
        string note_path PK
        string source
    }
```

### 本地同步状态

宿主使用 Outbox/Inbox 模型，不允许远程服务直接写入 Vault：

```mermaid
sequenceDiagram
    autonumber
    participant Watcher as Vault 观察器
    participant HostDB as host.sqlite3
    participant Sync as Sync Server
    participant Resolver as 冲突处理器
    participant Vault as Markdown Vault

    Watcher->>HostDB: 记录文件身份和待发送操作
    HostDB->>Sync: 携带基础修订上传操作
    Sync-->>HostDB: 返回远程修订或冲突
    alt 操作被接受
        HostDB->>HostDB: 推进 sync_heads 并完成任务
    else 发生冲突
        HostDB->>Resolver: 持久化本地与远程版本
        Resolver->>Vault: 执行保留本地、远程或副本选择
        Resolver->>HostDB: 记录处理结果并重试
    end
    Sync-->>HostDB: 下载有序远程 Inbox 项目
    HostDB->>Vault: 执行带日志且幂等的文件操作
    HostDB->>HostDB: 仅在持久完成后推进游标
```

## 仓库边界

| 路径 | 用途 |
| --- | --- |
| `frontend/` | Vue 3 应用与 Tauri/Rust 桌面宿主 |
| `frontend/src-tauri/` | 原生命令、能力权限、Sidecar 监督和 NSIS 打包配置 |
| `backend/` | FastAPI AI Core、检索、Agent、媒体处理、模型适配器和导出 |
| `backend/tests/` | 后端单元测试和集成测试 |
| `scripts/` | 构建、验收、验证与发布自动化 |
| `tools/` | 开发和打包辅助工具 |

公开组件分别维护：

| 组件 | 仓库 |
| --- | --- |
| 桌面主程序与 AI Core | [KiriAky107/OpenNexus](https://github.com/KiriAky107/OpenNexus) |
| Sync Server | [KiriAky107/Sync-for-OpenNexus](https://github.com/KiriAky107/Sync-for-OpenNexus) |
| 社区原型 | [KiriAky107/Community-for-OpenNexus](https://github.com/KiriAky107/Community-for-OpenNexus) |

本仓库不得混入 Sync Server 或社区服务的部署代码。跨仓库变更需要注明兼容版本，并分别发布。

## 安装

### Windows 安装包

1. 从 [GitHub Release](https://github.com/KiriAky107/OpenNexus/releases/tag/v0.5.2-alpha1) 下载 `OpenNexus_0.5.2-alpha1_x64-setup.exe`。
2. 校验 SHA-256：

   ```powershell
   Get-FileHash .\OpenNexus_0.5.2-alpha1_x64-setup.exe -Algorithm SHA256
   ```

   当前发布安装包的预期值为：

   ```text
   F1B88FDF1D3AE0B48B3D2907818A52B843E39BF94E94B4A913EC6AFD26EC8786
   ```

3. 运行安装程序，从开始菜单启动 OpenNexus，然后选择已有 Markdown 知识库或创建新知识库。
4. 打开“设置 → 模型提供商”，配置本地或远程模型。

安装包不包含用户知识库、已下载模型权重、CUDA 运行时或预装社区包。覆盖安装时会保留当前 Windows 用户位于 `%APPDATA%\cc.kronecker.notesagent` 的应用数据。

## 开发环境

### 环境要求

| 工具 | 支持基线 |
| --- | --- |
| Windows | Windows 10/11 x64 |
| Node.js | 22 或更高版本 |
| pnpm | 10.28.0 |
| Python | 3.11 或更高版本 |
| uv | 0.9.24 或兼容版本 |
| Rust | 当前稳定版工具链及 MSVC 目标 |
| WebView2 | 当前 Microsoft Edge WebView2 Runtime |

媒体处理、本地推理和部分导出路径可能需要额外运行时。仅为需要测试的功能安装这些组件，不要提交模型文件或运行时压缩包。

### 克隆并安装依赖

```powershell
git clone https://github.com/KiriAky107/OpenNexus.git
cd OpenNexus

cd backend
uv sync --frozen

cd ..\frontend
corepack enable
corepack prepare pnpm@10.28.0 --activate
pnpm install --frozen-lockfile
```

依赖锁定文件属于构建契约。修改依赖清单时，必须在同一个 Pull Request 中同步更新锁定文件。

### 启动开发环境

```powershell
# 终端一：AI Core
cd backend
uv run python scripts/dev-server.py

# 终端二：Web 前端
cd frontend
pnpm dev
```

进行桌面集成开发时，在 `frontend/` 中启动 Tauri 开发命令，并准备所需 Sidecar 产物。浏览器开发模式无法覆盖原生对话框、凭据保存、Sidecar 启动和安装路径，因此必须单独测试打包后的桌面应用。

### 常用前端命令

```powershell
cd frontend
pnpm type-check
pnpm test
pnpm build
pnpm build:report
```

## 配置与数据

- Vault 是由用户选择的目录，不属于应用源代码仓库。
- 模型提供商密钥必须保存在桌面凭据保险库中，不得写入源代码、截图、日志、测试夹具或 `localStorage`。
- 索引和生成缓存应当可以重建，不得作为笔记内容的唯一事实来源。
- 远程同步默认关闭。登录前应检查服务地址、TLS 配置、设备名和同步数据范围。
- 扩展包在完成清单、校验值、权限和可执行内容审查前，应视为不可信输入。
- 用于报告问题的日志必须脱敏；可保留必要的关联 ID，但应删除 Vault 正文、凭据、个人路径、主机名和个人信息。

## 测试

提交 Pull Request 前，应运行与改动层级相关的检查：

```powershell
# 后端测试
cd backend
uv run pytest

# 前端测试与生产构建
cd ..\frontend
pnpm test
pnpm type-check
pnpm build

# 原生宿主检查
cd src-tauri
cargo fmt --check
cargo test --all-targets --features desktop
cargo clippy --all-targets --features desktop -- -D warnings
```

跨进程或跨仓库变更还应运行 `scripts/` 下对应的验收脚本，并记录验收场景。发布候选版本至少应覆盖：启动、选择 Vault、模型提供商重启恢复、转录稿生成知识点笔记、Agent 规划、扩展安装、原生保存对话框、PDF/HTML/DOCX 导出，以及可选同步登录。

测试必须可重复，不得依赖贡献者的个人知识库或凭据，并应清理临时进程与文件。

## 打包与发布

在 `frontend/` 目录构建 Windows NSIS 安装包：

```powershell
pnpm desktop:build
```

桌面构建会先执行生产前端构建，并使用 `frontend/src-tauri/tauri.bundle.conf.json`。发布前需要：

1. 同步前端包、Tauri 配置、Rust 包和 Python 包元数据中的版本号。
2. 构建 AI Core Sidecar，确认桌面宿主启动的是匹配版本的产物。
3. 运行前端、后端、Rust 和发行验收检查。
4. 在干净的 Windows 用户环境中安装生成的 setup 程序。
5. 从安装后的应用验证原生文件对话框和 PDF、HTML、DOCX 导出。
6. 计算并发布 SHA-256 校验值。
7. 创建附注版本标签，并发布非草稿 GitHub Release。
8. 确保源码归档不包含 Vault、凭据、个人文档、生成缓存和其他服务仓库。

版本标签采用 `v<版本号>` 格式。Alpha 版本可以在明确说明状态的情况下作为普通 Release 发布，但发行说明必须保留兼容性限制。

## 安全与隐私

- 除非用户主动启用远程模型、同步或其他联网集成，否则 Vault 内容保留在本机。
- 模型凭据由桌面凭据保险库处理，不得由 WebView 持久化。
- 扩展权限、网络访问和高权限工具调用必须经过明确审查与授权。
- 原生命令必须验证路径，不得将文件访问范围扩大到所选 Vault 或用户明确操作之外。
- 只安装来自可信来源的 Skill、Plugin、主题和 MCP 服务。

不要在公开 Issue 中发布可利用的安全细节或真实密钥。请使用维护者私下联系方式，或在启用后使用 GitHub Private Vulnerability Reporting。

## 社区规范

OpenNexus 使用仓库级社区文件，让参与者在提交贡献前即可了解完整要求：

| 文档 | 用途 |
| --- | --- |
| [社区行为准则](CODE_OF_CONDUCT.zh-CN.md) | 参与和社区管理要求 |
| [贡献指南](CONTRIBUTING.zh-CN.md) | 分支、提交、工程、测试、文档和审查流程 |
| [安全策略](SECURITY.md) | 支持版本和私下漏洞报告方式 |
| [缺陷报告表单](.github/ISSUE_TEMPLATE/bug_report.yml) | 强制填写复现与脱敏信息 |
| [功能建议表单](.github/ISSUE_TEMPLATE/feature_request.yml) | 问题、结果、组件和影响分析 |
| [Pull Request 模板](.github/PULL_REQUEST_TEMPLATE.md) | 验证证据和审查检查清单 |

```mermaid
flowchart TD
    START[问题、缺陷、建议或漏洞] --> KIND{属于哪类反馈？}
    KIND -- 使用问题 --> DISCUSS[搜索 README 和已有 Issue]
    KIND -- 可复现缺陷 --> BUG[填写缺陷报告表单]
    KIND -- 范围明确的改进 --> FEATURE[填写功能建议表单]
    KIND -- 未修复漏洞 --> PRIVATE[使用私下安全报告]
    BUG --> TRIAGE[维护者分类并路由仓库]
    FEATURE --> TRIAGE
    TRIAGE --> ISSUE[形成包含范围和验收条件的 Issue]
    ISSUE --> BRANCH[建立聚焦的功能分支]
    BRANCH --> CHECKS[测试、文档、隐私和许可证检查]
    CHECKS --> PR[填写 PR 模板并接受审查]
    PR --> MERGE{是否满足要求？}
    MERGE -- 否 --> BRANCH
    MERGE -- 是 --> MAIN[合并到 main 并进入发布流程]
```

Sync Server 实现问题应提交到 [Sync-for-OpenNexus](https://github.com/KiriAky107/Sync-for-OpenNexus/issues)；社区目录、扩展包和原型问题应提交到 [Community-for-OpenNexus](https://github.com/KiriAky107/Community-for-OpenNexus/issues)。

## 参与开发

- 每次改动聚焦一个问题，并遵守上述仓库边界。
- 接口变化时，同步更新前端类型、后端 Schema、原生命令和测试。
- 使用锁定依赖，并说明新增运行时依赖的用途、许可证及打包影响。
- 提交信息采用 Conventional Commits，例如：

  ```text
  feat(agent): 持久化中断的规划任务
  fix(export): 修复安装版 PDF 渲染
  test(media): 覆盖转录稿生成知识点笔记
  docs(readme): 补充 Windows 发行校验说明
  ```

- 公共说明发生变化时，同时更新 `README.md` 和 `README.zh-CN.md`。
- 不得提交用户 Vault、个人文档、凭据、模型权重、构建产物或能识别参赛身份的材料。

## 许可证

OpenNexus 项目代码采用 [MIT License](LICENSE)。随项目分发或由用户下载的模型、程序库、字体、图标及其他第三方组件继续适用各自的许可证与声明；项目的 MIT 许可证不会覆盖或替代这些条款。

## Issue 要求

提交 Issue 前：

1. 搜索已开启和已关闭的 Issue，确认问题尚未被记录。
2. 使用具体标题，并注明是缺陷、功能建议、文档问题还是兼容性咨询。
3. 缺陷报告必须提供 OpenNexus 版本或提交号、Windows 版本、安装方式、受影响组件、预期结果、实际结果和最小复现步骤。
4. 仅附上定位问题所需的截图或日志，并删除 API Key、Token、Vault 正文、个人路径、账户信息、主机名和个人信息。
5. 说明问题出现在打包后的桌面应用、Web 开发模式，还是两者均可复现。
6. 提供相关模型/Provider、扩展、MCP 传输方式和 Sync Server 版本，但不得暴露凭据。
7. 安全漏洞和已经泄露的密钥必须私下报告，不得提交公开 Issue。

只有“无法使用”等笼统描述、缺少复现信息、重复提交或包含隐私数据的 Issue，可能会在补充或修正前被关闭。

## Pull Request 要求

Pull Request 必须满足：

1. 关联对应 Issue，或清楚说明改动动机和用户可见结果。
2. 只包含一个便于审查的逻辑改动；无关重构与生成文件必须拆分。
3. 从功能分支提交并使用 Conventional Commits；不得重写其他贡献者的分支，也不得强制更新默认分支。
4. 行为变化必须包含测试，并列出实际通过的命令和验收场景。
5. 同步更新两种语言的公共文档；行为、配置或兼容性变化还需更新发行说明。
6. 说明前端/后端/原生接口变化、数据迁移、回滚行为和跨仓库版本要求。
7. 说明每个新增依赖的用途、许可证、运行时体积和安装包影响。
8. UI 变更需提供前后对比截图，并删除全部个人信息和敏感信息。
9. 不得包含凭据、私人 Vault、个人文档、已下载模型、构建产物或能识别参赛身份的材料。
10. 请求审查前，应通过格式检查、类型检查、测试、生产构建和适用的桌面安装版验收。

欢迎使用 Draft Pull Request 进行早期技术讨论。仅在检查清单完成，且审查者无需访问私人基础设施或个人数据即可复现时，才应将 Pull Request 标记为 Ready for review。
