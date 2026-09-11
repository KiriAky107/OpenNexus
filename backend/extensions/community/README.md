# 社区扩展准备包

这是一组可以真实安装、启用、调用的扩展，非内置占位示例：

| 类型 | ID | 功能 |
| --- | --- | --- |
| Plugin | markdown-workbench | 标题、待办和格式检查；命令面板检查选中 Markdown |
| Skill | note-reviewer | 搜索并读取指定笔记，调用 Plugin，返回带行号的只读检查报告 |

在仓库根目录执行 `python backend/extensions/community/build_packages.py`，产物位于 `dist/`。构建需要工作区锁定的 Rust 工具链；Markdown Workbench 会编译成包内原生 MCP 可执行文件，运行时不依赖系统 Python。构建采用明确文件列表、固定 ZIP 时间戳和确定性链接参数，不打包缓存、密钥或本地环境。`dist/index.json` 提供类型、ID、版本、文件、大小、SHA-256 和依赖，可作为后续社区索引的数据样例；当前前端没有接入该社区索引。

先导入 Plugin ZIP 并启用，再导入 Skill ZIP 并启用。两种扩展都沿用现有 ZIP 安装入口；重启 AI Core 后仍需按当前运行时机制重新注册包。

未自动发布、创建远程仓库或指定新的开源许可证。正式发布前应确认许可证、托管下载地址、版本升级及签名策略。功能限制和使用步骤见各包 README。

开发服务器启用 `uvicorn --reload` 时，新解压的 `.py` 文件可能触发热重载并清空内存注册。此时可从 `backend/data/extension-packages/` 中已经解压的对应包目录重新安装、启用，避免重复解压；长期使用建议开发启动时排除运行数据目录的文件监听。
