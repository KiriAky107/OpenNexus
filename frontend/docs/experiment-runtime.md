# 实验运行时原型

2026-10-05：0.6.0 运行时与资源策略技术验证。这里记录随应用分发的运行时候选及原生隔离证据；解释器已通过实际 NSIS 解包和原生探测，实验执行入口尚未接入产品。

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

## 安装包资源与发现

[Windows bundle 配置](../src-tauri/tauri.bundle.conf.json)在编译前和最终打包前复核锁定运行时，并将完整资源及许可证保存到 `runtimes/python-3.13.16-windows-x64/`。[Host build](../src-tauri/build.rs)将该构建清单内嵌到 release Host；Windows x64 以外的目标不会混用此解释器。

[只读发现模块](../src-tauri/src/experiment_runtime.rs)从应用资源目录检查源码锁、文件清单、大小、SHA-256 和清单自身字节，拒绝额外文件、链接及被替换的 receipt。预期清单来自 Host 二进制；同时修改磁盘文件和旁边的 receipt 不能改变该预期。`host_capabilities` 记录实际发现结果，执行能力保持禁用，发现过程不运行脚本。此检查只证明发现时的完整性；正式运行仍须重新校验并在整个运行期间持有源码、解释器及祖先句柄。

[安装包验证](../../scripts/verify-windows-package.py)从真实 NSIS 产物解包，核对包内解释器与 Host 内嵌清单及所有文件。加 `--experiment-probe` 会编译独占测试驱动，以仅含 System32 的 `PATH`、无 Python 环境配置的父环境运行两个隔离探测；驱动明确记录 `sys.executable`，验证确实启动了解包后的解释器。30 个原生运行时文件另做 Authenticode 校验，证据保存到 `.build/windows/experiment-*/`。该探测不启动生产应用、不修改应用存储指针；`Windows package` CI 同时运行既有解包 Host 启动检查，并核对 Host 能发现包内运行时。

```powershell
python scripts/verify-windows-package.py --installer <本次构建的安装包> --experiment-probe
```

源码 `.py`、UTF-8 JSON／CSV 和标准库文本计算已通过原生夹具。桌面文件树现可在 Vault 根目录的 `experiments/` 下创建、编辑、重命名和同步 `.py`、`.json`、`.csv` UTF-8 文本；将 JSON 限定在该目录，避免把同名应用设置文件意外加入同步。桌面 Markdown 正文可用知识库相对链接打开实验文件，路径限制在 Vault 内；打开只进入纯文本编辑器，不会执行。CSV 可切换到只读表格预览，限于前 200 行、40 列、256K UTF-16 字符及每格 4096 字符；原文和保存内容不截断。旧版桌面客户端会忽略这些新类型，Sync 服务按既有通用文件协议存储字节。浏览器版暂不支持创建或编辑实验文件。执行确认、运行详情、日志及成果导入仍待接入。此处的安装包为实施中的技术产物，正式版本材料在整体验收后统一生成。

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
| 日志 | 256 KiB | 16–1024 KiB | stdout／stderr 各保留总预算的一半，超出仍排空；记录总字节、截断、解码损失和读取状态；产品接口待接入 |

[目录统计](../src-tauri/src/experiment_disk.rs)使用固定根目录句柄、相对目录 capability 和禁止跟随的对象打开。统计文件和目录的 NTFS DATA streams，并计入逻辑大小与预分配大小中的较大值；硬链接、重解析对象、未知对象、共享冲突和统计失败会停止运行，不作为零用量忽略。根目录不共享删除，子项可共享删除以支持原子重命名；每次扫描只相信实际打开的对象。

目录监控间隔为 100 ms 加扫描耗时，属于发现超限后终止的监控，不能承诺写入字节精确停在上限。快速写入后删除可能发生在两次扫描之间。容器注册表存储不属于文件目录统计，已用下述独立只读权限限制写入。独立墙钟 watchdog 不依赖目录扫描、UI 或授权轮询，仍能终止整个进程树。生产入口开放前必须继续评估瞬时写入风险，并完成并发、日志、输出、授权和安装包控制。

新的真实进程探测以 8 秒墙钟、2 秒用户态 CPU、256 MiB 内存、8 进程和 8 MiB 目录预算验证内存和进程耗尽、Temp 外的普通文件、文件隐藏流及目录隐藏流。CPU 死循环单独使用 1 秒 CPU／60 秒墙钟，核对真实 Job CPU 计数确实达到 CPU 预算；虚拟机调度等待不计作 CPU 时间。实验策略不采用扩展十秒窗口的 rate-pressure 通知，避免该通知和总 CPU 预算混淆；原有扩展策略保持独立。每种超限均得到对应底层资源错误且剩余进程为零；无额外工具计时器、无持续授权轮询时，8 秒 watchdog 也终止了已确认存在后代的进程树。`policy-probe-result.json` 保留实际限额、CPU 计数和耗时；底层复用的错误码仍使用 `EXTENSION_*`，后续结构化执行接口统一映射为实验状态。

CPU 时间的含义见 [Windows Job 基本限额](https://learn.microsoft.com/en-us/windows/win32/api/winnt/ns-winnt-jobobject_basic_limit_information)，文件流大小及分配大小见 [FILE_STREAM_INFO](https://learn.microsoft.com/en-us/windows/win32/api/winbase/ns-winbase-file_stream_info)。

## 私有注册表与权限审计

真实隔离 Python 探测证实：默认容器可以向自己的注册表写入并读回 16 MiB，文件目录计数仅为 4 KiB，因此完整目录扫描也不能覆盖该存储。直接叠加针对 package SID 的 deny ACE，在本机探测中仍未阻止私有键写入和修改 ACL；不能据此声称已隔离。最终的[注册表权限模块](../src-tauri/src/experiment_registry.rs)移除该新实例默认的完整 package ACE，再授予普通 `KEY_READ`，保留 Host、用户、SYSTEM 和其他原有主体的权限。

创建暂停进程之后、恢复之前，只处理 `Profile::create()` 生成的独占名称和 SID。Host 打开已存在的当前用户 profile storage，拒绝回退创建；遍历其全部既存子键，最多 64 键、16 层，逐键替换该 package 的完整权限。Windows 默认 `Children` 子键也有需单独处理的权限，不能只改根键。打开使用 `REG_OPTION_OPEN_LINK`，发现 `SymbolicLinkValue` 即拒绝，检查失败则销毁暂停进程。所有键句柄由 Job 持有，不交给执行进程；没有修改当前用户或系统的共享注册表 ACL。

新的 `storage-audit-result.json` 记录只针对合成选定输入／私有文件的 ACL 篡改负向、私有 registry location 与当前新实例名称的核对、根键写入／既有子键写入／修改 ACL／父存储写权限的拒绝。四类注册表操作均须得到 `ERROR_ACCESS_DENIED`，选定输入仍不能写入、未授权文件仍不能读取。另以 Host 写入作为正向控制，覆盖大键树和链接标记拒绝后仍能正常删除本次 profile；正常扩展容器未接入这项实验策略。

注册表位置 API 的行为见 [GetAppContainerRegistryLocation](https://learn.microsoft.com/en-us/windows/win32/api/userenv/nf-userenv-getappcontainerregistrylocation)，权限继承见 [SetSecurityInfo](https://learn.microsoft.com/en-us/windows/win32/api/aclapi/nf-aclapi-setsecurityinfo)。本机默认 ACE 的实际行为来自探测证据；最终固定提交的 Windows CI 也需通过相同负向检查。

CI 保存这份新增探测证据。[安装包核对脚本](../../scripts/verify-experiment-package.py)也将其列为必需回执，核对实际解释器的文件身份、版本与 isolated 模式、零残留进程、文件权限负向及四类注册表拒绝，并将三份 native 回执摘要纳入包验证结果。缺失证据或错误解释器不能被当作包内探测通过；这项核对在最终打包时执行，不因新增回执重复构建当前无改动的解释器。

## 真实输出捕获

[日志收集器](../src-tauri/src/experiment_log.rs)使用两个 Host 所有的匿名管道读线程，stdin 直接关闭为 EOF。按原始字节保留固定长度前缀，达到预算后继续读取并计数，避免脚本因满管道卡住；两个流不会相互挤占保留空间。展示时用 UTF-8 替换解码，并明确记录 `invalid_utf8`，保留边界切断一个多字节字符也属于解码损失。`complete` 只代表观察到 EOF，不能代替运行成功状态。

关闭先由运行所有者终止并等待整个 Job；最多再等待两秒自然排空。对仍阻塞的读线程反复调用 [CancelSynchronousIo](https://learn.microsoft.com/en-us/windows/win32/api/ioapiset/nf-ioapiset-cancelsynchronousio)，覆盖停止检查到进入读取之间的竞态，并等待线程退出。取消未读完的流标为不完整；非取消读取错误必须由运行观察者终止执行。未创建任意设备读线程或新的可访问管道名称。

普通回归测试重复二十次：确认两个读线程已进入真实原生 IO，对端写句柄始终保持打开，关闭仍在一秒内完成。隔离 Python 探测分别向 stdout 和 stderr 输出超过 2 MiB，在 16 KiB 总保留预算下完整计数、标记截断，保留中文并标记 stderr 的非法 UTF-8；另一探测在确认子进程持续向两个流写入后取消整个 Job，核对零残留进程与两流 EOF。证据只保存状态、字节数和有界摘要。

当前原型中 CPython 可能因未获授权的祖先目录无法解析解释器最终路径而向 stderr 写入启动诊断；测试保留并计入这条真实输出，不为了消除诊断而扩大目录读取权限。正式运行时布局和逐次持有对象句柄仍需在生产接入中核对。

## 生产接入前的依赖

[输入快照](../src-tauri/src/experiment_input.rs)已实现 Host 侧结构化请求校验：固定运行时 ID、知识库及操作 ID、入口和输入的稳定身份／相对路径／修订／内容哈希、已验证限额。入口只能为 `experiments/` 下的 `.py`，选定输入限定为该目录的 Python／JSON／CSV；不接受命令行、shell、环境变量、网络或任意解释器路径。每次最多 32 个文件，单文件 2 MiB、总内容 16 MiB，须为 UTF-8 文本。

准备快照时强制核对工作区当前身份和修订，再以持有的目录 capability 逐组件打开、拒绝跟随链接，并核对实际文件的类型、硬链接数、大小和 SHA-256。Windows 不共享对象写入／删除；Unix 使用非阻塞打开，防止被替换的 FIFO 在类型检查前挂起。只有读取并验证后的私有字节才能构造快照，后续人工编辑不能修改已准备的副本。输入排序固定，确认摘要的 fingerprint 绑定所有选定版本、知识库、运行时、预算及操作 ID；该摘要本身不授予执行权限。快照可由下述记录模块持久化；产品运行前的完整授权与原生资源绑定仍待接入。

[运行记录](../src-tauri/src/experiment_store.rs)将快照字节和等待确认的记录一起保存在知识库的 Host SQLite 事务中，沿用 WAL／FULL 同步；不产生同步 outbox，也不向执行进程开放数据库。schema 16 在旧库升级前保存独立备份。相同操作和相同 fingerprint 返回既有状态；修改预算或选定版本后复用旧操作 ID 会被拒绝。输入副本及记录正文累计限制 64 MiB、记录数限制 2048；单条结果正文最多 8 MiB。保留与清理的产品操作仍待接入。

确认只预留给可信用户界面路径，当前没有 Core／模型批准端点。批准前及实际领取执行前分别核对源文件，文件变化使旧请求失败并清除批准；领取还会逐个重新校验已持久化的快照字节。批准在五分钟后失效，必须重新确认。只有原子的 `approved → starting` 转换能领取一次任务，领取对象不能从 RPC 反序列化；全局并发许可和隔离启动的实际授权仍由后续运行器提供。

取消运行先记录 `cancel_requested`，待运行器确认整个 Job 停止后才保存取消结果；在完成事务之前已收到的取消优先。成功结果要求真实退出码 0、两流 EOF 且无读取错误；未知资源测量保留为空，不能伪造为 0。完成的首次结果保留，重复收尾不覆盖。重启将尚未启动的批准退回等待确认，并将 starting／running／cancel_requested 标为中断，不自动重放。上述状态与持久化目前通过合成工作区测试，产品运行／确认界面和真实运行器尚未接入。

运行授权必须绑定知识库、入口内容哈希、选定输入、固定运行时、限额和一次性确认；写源文件、运行、导入成果分别确认。进程只接收副本与运行 ID，成果通过 Host 清单和修订检查导回，不能直接写真实知识库。不能使用模型拼接的 shell 或普通用户权限的 subprocess 作为隔离失败后的替代路径。

下一批接入已验证的日志收集器，补齐输出清单、并发和取消状态，并继续处理扫描间隙的存储控制；随后贯通运行确认、Agent 工具与成果导入。最终安装包携带固定运行时及许可证，并在从包内解出的隔离副本上复用原生检查，确认没有开发目录依赖。
