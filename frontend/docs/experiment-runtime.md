# 实验运行时原型

2026-10-05：0.6.0 第一批技术验证。这里记录随应用分发的运行时候选及原生隔离证据；实验执行入口尚未接入产品，安装包交付验证仍待后续批次完成。

## 固定来源与构建

首个候选为官方 CPython 3.13.16 Windows x64 嵌入包，标准库支持 `.py` 源码、UTF-8 JSON 和 CSV 输入／输出。运行时不依赖用户的 Python、`PATH`、`PYTHONPATH`、site-packages 或既有冻结 Core；第三方科学计算依赖还未加入。

- [官方发行页](https://www.python.org/downloads/release/python-31316/)、[Windows 发行清单](https://www.python.org/ftp/python/3.13.16/windows-3.13.16.json)和[嵌入包说明](https://docs.python.org/3.13/using/windows.html#the-embeddable-package)。
- [来源锁](../../scripts/experiment-runtime-lock.json)固定 manifest 和 ZIP 的 SHA-256、归档大小、架构、版本和入口。ZIP 为 11,439,788 字节，展开为 35 个文件、22,642,444 字节；原包许可证随运行时保存。
- [准备脚本](../../scripts/prepare-experiment-runtime.py)先校验官方 HTTPS 下载，再解包。拒绝路径穿越、Windows 保留名／别名、重复名称、符号链接、硬链接、重解析目录及超量展开。复用缓存时从已锁定 ZIP 重建预期文件哈希，旁边的可修改 receipt 不能为篡改文件背书。
- `python313._pth` 仅启用 `python313.zip` 和当前运行时目录，不导入 site。启动参数固定为 `-I -B -X utf8`。嵌入解释器本身不构成安全隔离。

在仓库根目录执行：

```powershell
./scripts/verify-experiment-runtime.ps1
```

脚本先运行离线归档负向测试、准备运行时，再验证所有 EXE、DLL、PYD 的 Authenticode 状态，最后执行明确选择的原生 ignored 测试。输出位于忽略目录 `.build/experiment-runtime/`：`runtime.json` 位于运行时目录内，`signatures.json` 和 `probe-result.json` 位于其父目录。原生测试不接触真实知识库、应用凭据或生产安装。

GitHub CI 的 `windows-host` 使用同一入口并保存证据；普通 Linux Host 测试不下载或执行 Windows 运行时。默认测试不会误触发实验脚本执行。

## 隔离模型与原型验收

[原生测试](../src-tauri/src/experiment_runtime_probe.rs)为每次探测建立独占 AppContainer，无网络 capabilities。先创建暂停进程、绑定 Job、核对容器身份，再恢复执行。运行时与选定输入的读取句柄在整个运行期间保持打开，禁止共享写入或删除；单独授予该实例 SID 读取权限，退出后撤销授权。所选源码和 CSV 是测试副本，其他文件未授权。

| 场景 | 验收与证据 | 后续产品控制 |
| --- | --- | --- |
| 中文 CSV 计算 | 7 + 11 得到 JSON 中的 18，中文逐字保留；报告版本 3.13.16、isolated=1、site 未加载 | Host 校验源码版本、输入清单和运行时 ID |
| 修改选定输入 | 原生 PermissionError／errno 13；原 CSV 不变 | 只读副本及持有对象句柄，执行进程无知识库写入句柄 |
| 读取未选中的私有文件 | 未授权合成文件返回 PermissionError／errno 13 | AppContainer 文件 ACL；系统共享库可读，不承诺所有系统路径都不可读 |
| 继承父环境 | Host 设置的合成父 token 未出现在脚本环境 | 显式最小环境，不复制 Core／Host 凭据 |
| 网络 | Host 能连接同一个本地监听端口，容器连接失败且监听端未接收连接；记录实际 WinError 或超时类型 | 默认无网络 capabilities；正式执行器补齐协议和接口负向验证，不能仅把超时当作完整网络证明 |
| 取消子进程 | 已确认 Python 后代启动后终止整个 Job，剩余进程为零 | 取消、退出和失败只处理本次拥有的进程树 |
| 墙钟期限 | 750 ms 独立 watchdog 到期，终止进程并报告 deadline exceeded | 运行前启动 watchdog，不依赖 UI 持续轮询 |
| 内存、派生进程、磁盘写入 | 各自达到现有 512 MiB、16 进程、256 MiB scratch 限制后得到对应资源错误，整个 Job 退出 | 正式实验策略独立配置并限制并发、日志和总输出 |

原型还验证了两个需要在正式执行器解决的问题：Windows 为 CPython 提供的实际 TEMP 是容器 `AC/Temp`，自定义 TEMP 名称不能直接作为 Host 输出定位依据；脚本还可写容器自身其他目录，因此不能直接复用只统计 scratch 的磁盘预算。新执行器必须统计完整可写范围、保持独立权限和生命周期，并在资源策略不可用时拒绝启动。

## 生产接入前的依赖

运行授权必须绑定知识库、入口内容哈希、选定输入、固定运行时、限额和一次性确认；写源文件、运行、导入成果分别确认。进程只接收副本与运行 ID，成果通过 Host 清单和修订检查导回，不能直接写真实知识库。不能使用模型拼接的 shell 或普通用户权限的 subprocess 作为隔离失败后的替代路径。

下一批补齐全容器磁盘统计、独立资源配置、日志截断和取消状态；随后接文件编辑／同步契约与成果导入。最终安装包需要携带固定运行时及许可证，并在从包内解出的隔离副本上复用原生检查，确认没有开发目录依赖。
