# 实验运行时原型

2026-10-05：0.6.0 运行时与资源策略技术验证。这里记录随应用分发的运行时候选及原生隔离证据；实验执行入口尚未接入产品，安装包交付验证仍待后续批次完成。

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

脚本先运行离线归档负向测试、准备运行时，再验证所有 EXE、DLL、PYD 的 Authenticode 状态，最后串行执行两个原生 ignored 测试。输出位于忽略目录 `.build/experiment-runtime/`：`runtime.json` 位于运行时目录内，`signatures.json`、`probe-result.json` 和 `policy-probe-result.json` 位于其父目录。后两个文件分别记录既有扩展资源策略和独立实验资源策略。原生测试不接触真实知识库、应用凭据或生产安装。

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

Windows 为 CPython 提供的实际 TEMP 是容器 `AC/Temp`，自定义 TEMP 名称不能直接作为 Host 输出定位依据；脚本还可写容器自身其他目录，因此不能直接复用只统计 scratch 的磁盘预算。

## 独立实验资源策略

[Host 限额](../src-tauri/src/experiment_policy.rs)先验证结构化配置，再生成字段私有的已验证预算。未知字段、零值、越界和相互矛盾的请求均拒绝。模型不能通过配置添加网络 capabilities 或任意磁盘统计根目录。

| 项目 | 默认值 | 可配置范围 | 当前执行状态 |
| --- | --- | --- | --- |
| 墙钟时间 | 60 秒 | 1–120 秒 | 创建暂停进程前启动独立 watchdog；恢复执行前复核；到期终止整个 Job |
| 用户态 CPU 总时间 | 30 秒 | 1–60 秒且不超过墙钟 | Windows Job 按全部进程累计；监视线程也查询实际 CPU 用时；另保留约一个逻辑处理器的 CPU rate 上限 |
| Job 内存 | 256 MiB | 64–512 MiB | 内核限制分配，资源通知触发整个 Job 终止 |
| Job 进程数 | 4 | 1–8 | 内核限制全部派生进程，包含 Windows 实际创建的辅助进程 |
| 私有目录占用 | 64 MiB | 8–128 MiB | 统计整个独占容器包目录，包括 `AC/Temp` 及其他子目录和命名数据流 |
| 文件及目录数量 | 1024 | 32–2048 | 发现条目时即限制遍历队列；命名数据流总数另限为该值的 16 倍 |
| 最终输出 | 16 MiB | 1 MiB 至目录预算 | 已验证配置；成果清单／导入时的执行控制待接入 |
| 日志 | 256 KiB | 16–1024 KiB | 已验证配置；stdio 截断及其状态待接入 |

[目录统计](../src-tauri/src/experiment_disk.rs)使用固定根目录句柄、相对目录 capability 和禁止跟随的对象打开。统计文件和目录的 NTFS DATA streams，并计入逻辑大小与预分配大小中的较大值；硬链接、重解析对象、未知对象、共享冲突和统计失败会停止运行，不作为零用量忽略。根目录不共享删除，子项可共享删除以支持原子重命名；每次扫描只相信实际打开的对象。

目录监控间隔为 100 ms 加扫描耗时，属于发现超限后终止的监控，不能承诺写入字节精确停在上限。快速写入后删除可能发生在两次扫描之间；容器注册表存储也不属于文件目录统计。独立墙钟 watchdog 不依赖目录扫描、UI 或授权轮询，仍能终止整个进程树。生产入口开放前必须继续评估这些存储范围与瞬时写入风险，并完成并发、日志、输出、授权和安装包控制。

新的真实进程探测以 8 秒墙钟、2 秒用户态 CPU、256 MiB 内存、8 进程和 8 MiB 目录预算验证内存和进程耗尽、Temp 外的普通文件、文件隐藏流及目录隐藏流。CPU 死循环单独使用 1 秒 CPU／60 秒墙钟，核对真实 Job CPU 计数确实达到 CPU 预算；虚拟机调度等待不计作 CPU 时间。实验策略不采用扩展十秒窗口的 rate-pressure 通知，避免该通知和总 CPU 预算混淆；原有扩展策略保持独立。每种超限均得到对应底层资源错误且剩余进程为零；无额外工具计时器、无持续授权轮询时，8 秒 watchdog 也终止了已确认存在后代的进程树。`policy-probe-result.json` 保留实际限额、CPU 计数和耗时；底层复用的错误码仍使用 `EXTENSION_*`，后续结构化执行接口统一映射为实验状态。

CPU 时间的含义见 [Windows Job 基本限额](https://learn.microsoft.com/en-us/windows/win32/api/winnt/ns-winnt-jobobject_basic_limit_information)，文件流大小及分配大小见 [FILE_STREAM_INFO](https://learn.microsoft.com/en-us/windows/win32/api/winbase/ns-winbase-file_stream_info)。

## 生产接入前的依赖

运行授权必须绑定知识库、入口内容哈希、选定输入、固定运行时、限额和一次性确认；写源文件、运行、导入成果分别确认。进程只接收副本与运行 ID，成果通过 Host 清单和修订检查导回，不能直接写真实知识库。不能使用模型拼接的 shell 或普通用户权限的 subprocess 作为隔离失败后的替代路径。

下一批补齐日志截断、输出清单、并发和取消状态，并评估私有注册表和扫描间隙的存储控制；随后接文件编辑／同步契约与成果导入。最终安装包需要携带固定运行时及许可证，并在从包内解出的隔离副本上复用原生检查，确认没有开发目录依赖。
