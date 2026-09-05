# 浏览器回归入口

先在 frontend 运行 `pnpm dev`。这些页面直接挂载真实组件，使用空的编辑器状态，不加载或保存用户笔记，也不调用模型 API；不在生产构建的入口中。

- `/tests/visual/index.html?theme=light`：将 theme 依次替换为 dark、sepia、paper-moments、ocean-blue、midnight-purple。检查输入、禁用、焦点、悬停、展开/折叠、Markdown、图表及长标识；在宽屏和窄窗口重复。
- `/tests/visual/index.html?case=dialog`：检查满视口遮罩、背景无法滚动、内部长内容可滚动、Tab 焦点限定、Escape 关闭与再次打开。
- `/tests/visual/index.html?case=editor`：逐字输入行内代码；先输入两个反引号、向左移再填字；连续普通/软换行；输入法提交；选区替换；撤销/重做。编辑器单元测试另覆盖 Markdown 序列化往返。

运行自动回归：`pnpm test`。AppDialog 测试覆盖滚动锁引用计数、恢复焦点、禁止隐式关闭；主题预览矩阵覆盖六主题的实际共享 CSS、控件状态和 CSP/无脚本隔离。自动结构检查不代替浏览器截图、布局和对比度检查。
