"""OpenNexus Core 的 PyInstaller 早期 Windows 兼容处理。"""
from __future__ import annotations

import platform
import sys


# Python 3.13 的 platform 模块会优先查询 WMI。个别 Windows 环境的 WMI
# Provider 会无限等待，而冻结程序可能在入口模块执行前就间接调用 platform.system()。
# 禁用私有 WMI 加速入口后会回退到注册表/API 路径，返回值语义保持不变。
if sys.platform == "win32" and hasattr(platform, "_wmi"):
    platform._wmi = None
