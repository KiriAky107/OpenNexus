"""Export Service：多格式文档导出（首批 HTML）。

模块划分：
- document.py        Document AST 内部协议 + DocumentExporter Protocol + ExportResult
- markdown.py        mistune → Document AST 解析
- exporters/html.py  HtmlExporter（Document AST → HTML5）
- service.py         导出任务注册表、后台执行、取消与文件生命周期
"""
