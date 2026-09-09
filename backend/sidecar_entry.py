"""PyInstaller 条目；应用程序包包含在构建脚本中。"""
from app.sidecar import main

if __name__ == "__main__":
    raise SystemExit(main())
