# 长文渲染压测

使用真实无头 Chrome 和 Chrome DevTools Protocol，加载当前 Vite 工作区。样本由 `fixture.js` 确定性生成，包含至少 25000、60000、120000 汉字、H1–H3、表格、代码块、提示框、链接和行内格式，不读写真实 Vault。

## 运行

仓库根目录启动独立服务：

```powershell
npm --prefix frontend run dev -- --port 5175 --strictPort
```

在另一个终端执行（Python 环境需安装 websockets）：

```powershell
backend/.venv/Scripts/python.exe frontend/tests/performance/run-stress.py --url http://127.0.0.1:5175/tests/performance/stress.html --runs 3 --output .local-plans/stress-results.json
```

可用 `--chrome` 指定 Chromium 路径，用 `--sizes 25000 60000 120000` 指定样本。脚本使用独立临时浏览器配置，结束后关闭测试进程；不接管用户 Chrome。样本在每次导航后重建，不向后端保存。

## 指标口径

- openMs：组件挂载到编辑器完成初始化及两个 animation frame；不包含模块下载与 Vite 编译。
- selectionMs：30 次光标选区事务的同步耗时；insertMs：20 次插入“压测输入”的事务耗时。
- foldMs：6 次全折叠/展开按钮操作的同步耗时。
- previewMs：同一正文静态渲染三次，包含 HTML 插入与两个 animation frame。第一次包含首次高亮初始化，后两次为热运行。
- longTasks：浏览器 Long Tasks API，包含整个测量过程；heapUsedBytes 为单次采样，不代表峰值或泄漏结论。
- integrity：序列化输出保留插入内容与文末标记。常规单元测试另外覆盖 Markdown 往返。

这些是开发模式下的微基准，不等于真实键盘/输入法的端到端延迟，不覆盖滚动帧率、自动保存网络、向量计算或 Mermaid 图表压力。不同机器、后台负载和缓存状态会影响结果，不能把一次结果作为通用 SLA。重型图表应单独使用已有 `tests/visual/mermaid-matrix.html` 验证。

打开 stress.html 后也可通过控制台调用 `await runBenchmark(25000)` 查看 JSON 结果。页面只用于测试，不在正式路由中注册。

## 连续滚轮与折叠定位

```powershell
backend/.venv/Scripts/python.exe frontend/tests/performance/run-stress.py --url http://127.0.0.1:5175/tests/performance/stress.html --scroll --runs 2 --output .local-plans/scroll-results.json
```

`--scroll` 使用纸间时光主题及高度受限的编辑区，派发 120 次真实 CDP 滚轮事件（先向下再向上），记录 animation frame 间隔与长任务；随后从文末全部折叠，记录滚动位置和光标位置。可追加 `--profile --sizes 120000 --runs 1` 保存 CPU profile，用 Chrome DevTools Performance 面板导入。采样会增加开销，勿将 profile 结果与无采样结果直接比较。

帧间隔包含无头浏览器、CDP 调度和布局开销，不等同于用户设备的 FPS。滚轮模式不验证输入法、保存或图表渲染。当前测试容器改为有限高度的 flex 布局，早期普通事务报告使用的容器布局不同，跨版本比较应分别保留同一布局下的基线。

主题对比使用 URL 查询参数：`stress.html?theme=light`、`?theme=dark`，默认是 `paper-moments`。诊断参数 `?variant=no-outline` 可关闭编辑区轮廓线，用于隔离旧版纸间时光的长文开销；1.8.1 已不再使用这条 outline。`--screenshot` 会在派发滚轮前保存当前视口 PNG，截图时间可能计入记录区间。

## Agent 与任务

`agent-task.html?kind=tasks&theme=light` 测试任务组件，`kind=trace` 测试 Trace；支持 light、dark、paper-moments。继续使用 `run-stress.py --scroll`，任务规模可设 `--sizes 100 1000`，Trace 可设 `--sizes 200 2000 10000`。每个规模在新页面中生成独立数据，所有 fetch 被拦截，未知请求直接失败，不落到真实后端。

任务先记录实际分页加载数量，再注入全量夹具测渲染上限，结果包含 `fullListIsInjected`。Trace 测量时间线、树形搜索及切换；滚轮区间与过滤区间分别计时。`scrollContainers` 和 `maxScrollTop` 用来确认目标实际滚动。

修复后任务会读取所有 API 页，渲染每页 100 条；Trace 每页 200 条，筛选仍覆盖完整数据。`renderedTasks`、`totalFilteredCount` 区分 DOM 数量与实际记录总数，不能把分页后的 DOM 数量误报为数据丢失。

`logs.html?theme=paper-moments` 使用隔离的合成日志，支持 `light`、`dark` 主题，用于筛选栏、日志详情和主题视觉检查。可搭配驱动的 `--scroll --screenshot --sizes 1 --runs 1` 保存首屏；该夹具不连接真实日志库，不用于测后端日志吞吐。
