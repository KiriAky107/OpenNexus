
# OpenNexus 贡献指南

**简体中文** | [English](https://www.google.com/search?q=CONTRIBUTING.md&utm_source=gemini)

---

感谢你参与建设与改进 OpenNexus！我们非常欢迎社区提交代码、完善技术文档、优化系统架构以及提供高质量的可复现缺陷报告。

OpenNexus 是一个注重隐私安全与本地优先（Local-First）的桌面端知识工作区，融合了基于 Tauri 的 Rust 原生宿主、基于 FastAPI 的 Python AI 伴生引擎，以及 Vue 3 响应式前端。为了保障系统的长期稳定性、用户数据主权与工程迭代效率，所有贡献均须遵守以下工程标准与规范。

---

## 1. 核心准则与代码仓库边界

在开始编写代码或提交提案前，请确认你的改动属于当前仓库的职责范畴：

| 职责范畴 | 所属仓库 | 核心技术栈 |
| --- | --- | --- |
| **桌面客户端与 AI 引擎** | **[OpenNexus](https://github.com/KiriAky107/OpenNexus?utm_source=gemini)** *(当前仓库)* | Tauri 2 (Rust)、Vue 3、FastAPI |
| **端到端加密同步服务** | [Sync-for-OpenNexus](https://github.com/KiriAky107/Sync-for-OpenNexus?utm_source=gemini) | 独立服务端、PostgreSQL、S3 |
| **扩展中心与技能注册表** | [Community-for-OpenNexus](https://github.com/KiriAky107/Community-for-OpenNexus?utm_source=gemini) | Manifest 协议、包分发元数据 |

* **提议先行 (Issue First)**：对于涉及底层架构变动、数据库结构迁移（Schema Migrations）、跨进程协议调整或重大破坏性变更，请先 [提交 Issue](https://www.google.com/search?q=https://github.com/KiriAky107/OpenNexus/issues/new/choose&utm_source=gemini) 探讨技术方案与折中取舍，避免无效编码。
* **脱敏原则 (Synthetic Data Only)**：严禁在代码、测试夹具（Fixtures）或日志中包含真实用户笔记、生产 API 密钥、私有 IP 或个人敏感数据。所有测试数据必须使用人工生成的 Mock 数据。

---

## 2. 开发工作流

### 第一步：分支管理

基于最新的 `main` 主分支切出独立的特性分支：

```bash
git checkout main
git pull origin main
git checkout -b <type>/<short-description>

```

* **新功能分支**：`feat/agent-checkpoint-resume`
* **问题修复分支**：`fix/pdf-export-timeout`
* **性能优化分支**：`perf/hybrid-retrieval-rerank`
* **文档维护分支**：`docs/mcp-configuration-guide`

### 第二步：依赖安装

严格根据仓库锁定的依赖版本进行初始化：

```powershell
# 安装 Python AI 核心依赖
cd backend
uv sync --frozen

# 安装前端与桌面框架依赖
cd ..\frontend
corepack enable
corepack prepare pnpm@10.28.0 --activate
pnpm install --frozen-lockfile

```

### 第三步：原子化提交

保持单个 Pull Request 职责单一。请勿在一个 PR 中混合格式化重构、业务逻辑修改与无关代码微调。

---

## 3. 工程设计原则

任何代码贡献均须恪守 OpenNexus 的底层设计约束：

### 本地优先与信任边界

* **Rust 宿主拥有最高系统特权**：涉及越界文件读写、OS 原生凭证存储、系统级原生弹窗、子进程生命周期调度等敏感操作，必须且只能在 Tauri 宿主层（`frontend/src-tauri/`）中执行。
* **AI Core 属于受限伴生服务**：FastAPI 进程仅通过带鉴权的本地回环接口提供计算、检索与转写能力，绝不可直接绕过宿主处理敏感凭据或执行任意磁盘写操作。
* **前端为不可信环境**：Vue WebView 仅负责交互展示与发起结构化命令，内存或 `localStorage` 中严禁直接保存第三方 API 密钥明文。

### 类型同步与契约一致性

凡涉及 IPC 通讯命令、请求体或返回数据结构的调整，必须同步更新以下四个层次：

1. 后端模型契约（`backend/` 下的 Pydantic 模型）。
2. Rust 原生命令签名与错误枚举（`src-tauri/src/`）。
3. 前端 TypeScript 类型定义（`frontend/src/`）。
4. 对应的单元测试与契约测试。

### 幂等性与持久化保障

* **操作原子化**：文件写入、媒体解析转换任务、扩展包安装等关键操作必须具备幂等性与崩溃一致性。进程意外中断后再次启动，不可产生残留孤儿状态或脏数据。
* **追加式迁移（Append-Only）**：针对 `app.db` 或宿主 SQLite 数据库的 Schema 变更，必须提供结构化迁移脚本，严禁对用户已有数据执行不可逆的破坏性变更。

### 第三方依赖治理

在 PR 中引入任何新依赖包时，需在描述中清晰说明：引入动机、运行时开销、对打包安装包体积的影响，以及开源协议合规性（首选 MIT、Apache-2.0 等宽松协议）。

---

## 4. Git 提交规范

本项目遵循 [Conventional Commits](https://www.conventionalcommits.org/zh-hans/?utm_source=gemini) 规范：

```text
<type>(<scope>): <简要总结（祈使语气）>

[可选正文：详细说明变更背景、技术动机与工程权衡]

[可选页脚：关联 Issue，如 Closes #123, Breaking Changes]

```

### 常用类型标识

* `feat`：新增功能（面向用户或开发者）。
* `fix`：修复 Bug。
* `perf`：提升检索、推理或界面渲染性能的代码改动。
* `refactor`：既不修复 Bug 也不添加功能的代码重构。
* `test`：新增测试用例或修正已有测试。
* `docs`：仅包含文档类变动。
* `build` / `ci`：影响构建系统、外置依赖或 CI 流程的变动。

### 提交示例

```text
feat(agent): persist checkpoint states during long-running tasks
fix(export): prevent crash when compiling math blocks to PDF
docs(setup): clarify Rust MSVC baseline requirement on Windows

```

---

## 5. 质量校验矩阵

在提交代码或请求审查之前，请确保运行并通过对应层级的测试与静态检查：

```powershell
# 1. AI 核心与后端测试
cd backend
uv run pytest

# 2. 前端类型检查、单元测试与打包分析
cd ..\frontend
pnpm type-check
pnpm test
pnpm build

# 3. Tauri / Rust 宿主原生端代码规范与测试
cd src-tauri
cargo fmt --check
cargo test --all-targets --features desktop
cargo clippy --all-targets --features desktop -- -D warnings

```

* **桌面集成验收**：凡涉及原生弹窗交互、伴生进程生命周期管理、PDF/DOCX 导出或安装包配置的变动，需通过本地完整打包运行验证（`pnpm desktop:build`）。
* **文档语法检查**：Markdown 内部相对链接必须保持有效，代码围栏格式正确，Mermaid 图表在 GitHub 上能够正常无错解析。

---

## 6. Pull Request 提交与评审

1. **发起 PR**：在描述中通过关键词关联相关 Issue（如 `Closes #123`、`Fixes #456`）。
2. **完善说明**：清晰阐明“问题背景”、“解决方案”以及“验证结果”（可附带终端测试日志或 UI 前后对比动图/截图）。
3. **保持中英文档同步**：当公开交互、环境配置或命令发生变更时，请务必同步更新 `README.md` 与 `README.zh-CN.md`。
4. **积极跟进评审**：维护者将围绕代码正确性、安全信任边界、运行性能和可维护性展开 Code Review。评审阶段可直接推送新 Commit，合并时将统一进行 Squash 合并。

---

## 7. 安全报告与社区准则

* **漏洞提报**：切勿在公开 Issue 或 PR 中披露未修补的安全漏洞或真实系统密钥。请通过 [GitHub 私密漏洞提报通道](https://www.google.com/search?q=https://github.com/KiriAky107/OpenNexus/security/advisories/new&utm_source=gemini) 提交。
* **行为准则**：所有参与 OpenNexus 社区的成员均受 [社区行为准则](https://www.google.com/search?q=CODE_OF_CONDUCT.zh-CN.md&utm_source=gemini) 约束。请保持理性、严谨、包容与尊重的沟通氛围。
