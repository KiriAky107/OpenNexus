# OpenNexus for Android 0.1.0-alpha1

## English

This first Android alpha brings OpenNexus notes and free Canvas editing to phones and tablets. It reuses the desktop writing and Canvas components, with compact navigation and formatting controls that adapt to the available width.

- Write Markdown in source or rich text, review its rendered content, and recover a draft after returning to the app or starting a new process. Saving, renaming and deletion require their own explicit actions.
- Edit Canvas text, file, link and image nodes, connections and properties. Pan, pinch, move and resize with touch; keep the original JSON fields and attachment references when editing existing desktop content.
- Select original images through Android's document picker and export Markdown, Canvas and their original attachments as content. A content export does not include SQLite history, credentials or an application backup.
- Review an original local or external link before opening it. HTTP scheme capitalization is normalized only for the Android handoff; the reviewed address, note/Canvas bytes, query encoding and fragment remain intact.
- Use Sync v1 with Sync for OpenNexus 0.8.0 from an explicit foreground action. Review conflicts and the original operation result before retrying an uncertain request. Sign-in credentials stay in the native credential store; the editor has no credential-read command.
- Select Chinese or English and use the shared theme controls across phone and tablet layouts. Document content, names and paths keep their original text.

The phone package is a split ARM64 APK, with Android 7.0/API 24 or later required and API 36 targeted. The stable application ID is `cc.kronecker.opennexus`; earlier `.dev` QA builds use separate private storage. Updating the stable app requires the same signing identity and an increasing versionCode. Uninstalling removes its private data, so export content before removing it.

This is an early Android release. Physical phone/OEM coverage is still being expanded; hosted emulator results are identified separately from real device results. Sync runs through an explicit foreground action. Keep a content export before uninstalling or moving from a separate QA application ID.

## 简体中文

首个 Android alpha 将 OpenNexus 笔记与画布自由编辑带到手机和平板。它复用桌面的写作与画布组件，导航和格式控件随可用宽度调整。

- 使用源码或富文本编辑 Markdown、查看渲染内容，在返回应用或新进程启动后恢复草稿。保存、改名和删除分别由明确操作触发。
- 编辑画布的文本、文件、链接、图片节点、连线和属性，通过触控平移、缩放、移动和调整大小。编辑既有桌面内容时保留 JSON 原字段与附件引用。
- 通过 Android 文件选择器导入原图，将 Markdown、画布和原附件导出为内容文件。内容导出不包含 SQLite 历史、凭据或应用备份。
- 审核本地或外部链接的原始地址后再打开。HTTP 协议名大小写仅在交给 Android 时规范化，审核原地址、笔记/画布原字节、查询编码与片段保持不变。
- 明确在前台发起 Sync v1 同步，与 Sync for OpenNexus 0.8.0 配合使用。重试结果未知的请求前，先核对冲突与原操作结果。登录凭据留在原生凭据存储，编辑器没有读取凭据的命令。
- 手机和平板布局共用主题控件，支持选择中文或英文。正文、名称和路径保持原文。

手机安装包为独立 ARM64 APK，要求 Android 7.0/API 24 或以上，目标 API 36。稳定应用标识为 `cc.kronecker.opennexus`，此前 `.dev` QA 包使用独立的私有存储。稳定应用更新要求相同签名身份与递增的 versionCode。卸载会清除私有数据，移除应用前应先导出内容。

这是 Android 早期版本，真机和 OEM 覆盖仍在扩充；托管模拟器与真实设备的结果分别标明。同步由明确的前台操作触发。卸载或从独立 QA 应用标识迁移前，应先保留内容导出。
