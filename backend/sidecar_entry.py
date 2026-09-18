"""PyInstaller 条目；分派桌面 Core 与受限的内部工作进程。"""
from __future__ import annotations

from pathlib import Path
import sys


PDF_RENDER_WORKER = "--opennexus-pdf-render"


def main(argv: list[str] | None = None) -> int:
    """冻结程序不能使用 ``python -m``，因此在固定入口显式分派 PDF Worker。"""
    args = sys.argv if argv is None else argv
    if len(args) == 5 and args[1] == PDF_RENDER_WORKER:
        from app.export.browser_pdf import print_snapshot

        print_snapshot(Path(args[2]), Path(args[3]), args[4])
        return 0
    from app.sidecar import main as sidecar_main

    return sidecar_main()

if __name__ == "__main__":
    raise SystemExit(main())
