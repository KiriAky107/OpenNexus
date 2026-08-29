import type { FileNode } from '@/contracts'

// Mock workspace service for web dev mode
// In Tauri environment this will use Tauri IPC commands

export interface VaultInfo {
  path: string
  name: string
}

const MOCK_VAULTS: VaultInfo[] = [
  { path: '/Users/demo/Documents/MyVault', name: '我的知识库' },
  { path: '/Users/demo/Documents/StudyNotes', name: '学习笔记' },
]

const MOCK_FILE_TREE: FileNode[] = [
  {
    id: 'f-data',
    name: '数据结构',
    path: '/数据结构',
    type: 'folder',
    is_open: true,
    children: [
      { id: 'n-rbt', name: '红黑树.md', path: '/数据结构/红黑树.md', type: 'file' },
      { id: 'n-bst', name: '二叉搜索树.md', path: '/数据结构/二叉搜索树.md', type: 'file' },
      {
        id: 'f-list',
        name: '链表',
        path: '/数据结构/链表',
        type: 'folder',
        is_open: false,
        children: [
          { id: 'n-slist', name: '单链表.md', path: '/数据结构/链表/单链表.md', type: 'file' },
          { id: 'n-dlist', name: '双向链表.md', path: '/数据结构/链表/双向链表.md', type: 'file' },
        ],
      },
    ],
  },
  {
    id: 'f-os',
    name: '操作系统',
    path: '/操作系统',
    type: 'folder',
    is_open: false,
    children: [
      { id: 'n-deadlock', name: '死锁.md', path: '/操作系统/死锁.md', type: 'file' },
      { id: 'n-sched', name: '进程调度.md', path: '/操作系统/进程调度.md', type: 'file' },
    ],
  },
  {
    id: 'f-net',
    name: '计算机网络',
    path: '/计算机网络',
    type: 'folder',
    is_open: false,
    children: [
      { id: 'n-tcp', name: 'TCP_IP.md', path: '/计算机网络/TCP_IP.md', type: 'file' },
      { id: 'n-http', name: 'HTTP协议.md', path: '/计算机网络/HTTP协议.md', type: 'file' },
    ],
  },
  { id: 'n-welcome', name: '欢迎使用知笔知己.md', path: '/欢迎使用知笔知己.md', type: 'file' },
]

export function getRecentVaults(): Promise<VaultInfo[]> {
  return Promise.resolve(MOCK_VAULTS)
}

export function openVault(path: string): Promise<VaultInfo> {
  const name = path.split(/[/\\]/).filter(Boolean).pop() || 'Vault'
  return Promise.resolve({ path, name })
}

export function createVault(path: string, name: string): Promise<VaultInfo> {
  return Promise.resolve({ path, name })
}

export function getFileTree(): Promise<FileNode[]> {
  return Promise.resolve(JSON.parse(JSON.stringify(MOCK_FILE_TREE)))
}

export function readFileContent(filePath: string): Promise<string> {
  const name = filePath.split('/').pop() || 'Untitled'
  if (name === '欢迎使用知笔知己.md') {
    return Promise.resolve(`# 欢迎使用知笔知己

这是一款本地优先的 AI 笔记软件，支持 Markdown 编辑、智能检索、RAG 问答和 Agent 助手。

## 核心特性

- **本地优先**：所有笔记以 Markdown 格式保存在本地，数据完全由你掌控
- **混合检索**：FTS5 全文检索 + 向量语义检索，精准定位知识
- **AI 问答**：基于 RAG 技术，让 AI 基于你的笔记回答问题
- **Agent 助手**：通过工具调用，AI 可以帮你管理笔记、创建任务
- **Skill 系统**：将常用 AI 工作流保存为可复用的 Skill
- **插件扩展**：通过 Plugin 扩展应用能力

## 快速开始

1. 在左侧文件树中创建你的第一篇笔记
2. 使用 \`Ctrl+P\` 打开命令面板
3. 使用搜索功能快速找到你的笔记
4. 打开 AI 对话，开始与你的知识对话

> 提示：你可以在设置中配置你的模型提供商，开始使用 AI 功能。

## 编辑器模式

- **所见即所得模式**：使用 Milkdown 提供流畅的 Markdown 编辑体验
- **源码模式**：使用 CodeMirror 6 编辑原始 Markdown 源码

点击右上角按钮可以切换编辑模式。

## 代码示例

\`\`\`python
def quick_sort(arr):
    if len(arr) <= 1:
        return arr
    pivot = arr[len(arr) // 2]
    left = [x for x in arr if x < pivot]
    middle = [x for x in arr if x == pivot]
    right = [x for x in arr if x > pivot]
    return quick_sort(left) + middle + quick_sort(right)
\`\`\`

## 任务列表

- [x] 完成项目初始化
- [x] 设计技术架构
- [ ] 实现前端界面
- [ ] 接入后端 AI Core
- [ ] 性能优化与测试

---

祝你写作愉快！
`)
  }
  if (name === '红黑树.md') {
    return Promise.resolve(`# 红黑树

红黑树（Red-Black Tree）是一种自平衡二叉搜索树，每个节点带有颜色属性（红色或黑色）。

## 性质

1. 每个节点是红色或黑色
2. 根节点是黑色
3. 所有叶子节点（NIL）是黑色
4. 如果一个节点是红色，则它的两个子节点都是黑色
5. 从任一节点到其每个叶子的所有简单路径都包含相同数目的黑色节点

这些性质确保了红黑树的关键特性：**从根到叶子的最长可能路径不会超过最短可能路径的两倍长**。

## 插入操作

插入后可能破坏红黑性质，需要通过变色和旋转来修复。

### 情况1：叔叔节点是红色

将父节点和叔叔节点设为黑色，将祖父节点设为红色，当前节点上移到祖父节点，继续向上调整。

### 情况2：叔叔节点是黑色，且当前节点是右孩子

以父节点为支点左旋，将当前节点转换为左孩子，进入情况3。

### 情况3：叔叔节点是黑色，且当前节点是左孩子

以祖父节点为支点右旋，将父节点设为黑色，祖父节点设为红色。

## 与 AVL 树对比

| 特性 | AVL 树 | 红黑树 |
|------|--------|--------|
| 平衡严格度 | 高度差 ≤ 1 | 黑色高度相同 |
| 查找速度 | 更快 | 略慢 |
| 插入删除 | 旋转更多 | 旋转更少 |
| 适用场景 | 读多写少 | 读写均衡 |

## 应用场景

- C++ STL 的 map/set
- Java 的 TreeMap
- Linux 内核的完全公平调度器
`)
  }
  return Promise.resolve(`# ${name.replace('.md', '')}

这是一篇示例笔记。

## 第一部分

这里是笔记的内容。

## 第二部分

更多内容...

> 引用内容示例

\`\`\`javascript
console.log('Hello, Notes Agent!');
\`\`\`
`)
}

export function saveFileContent(filePath: string, content: string): Promise<void> {
  console.debug(`[workspaceService] Save ${filePath}, ${content.length} chars`)
  return Promise.resolve()
}

export function createFile(folderPath: string, name: string, content = ''): Promise<FileNode> {
  const path = `${folderPath}/${name}`
  const id = `n-${Date.now()}`
  return Promise.resolve({ id, name, path, type: 'file' })
}

export function createFolder(parentPath: string, name: string): Promise<FileNode> {
  const path = `${parentPath}/${name}`
  const id = `f-${Date.now()}`
  return Promise.resolve({ id, name, path, type: 'folder', is_open: true, children: [] })
}

export function renameFile(oldPath: string, newName: string): Promise<void> {
  return Promise.resolve()
}

export function deleteFile(path: string): Promise<void> {
  return Promise.resolve()
}

export function moveFile(sourcePath: string, targetPath: string): Promise<void> {
  return Promise.resolve()
}
