"""Function Plot：函数图像的白名单表达式解析与静态 SVG 渲染。

模块划分：
- model.py    FunctionPlot 等内部数据模型（不进 contracts.py，同 Document AST）
- parser.py   function-plot 源码与表达式解析（ast 白名单，绝不 eval/exec）
- render.py   把 FunctionPlot 渲染为内嵌 SVG（纯几何 + <text>，无脚本）
"""
