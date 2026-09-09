"""检查仓库 Markdown 相对链接；忽略远程 URL、页内锚点与示例占位。"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
LINK = re.compile(r"(?<!!)\[[^\]]*\]\(([^)]+)\)")


def main() -> int:
    failures: list[str] = []
    for document in ROOT.rglob("*.md"):
        if any(part in {".build", ".venv", "node_modules", "target"} for part in document.parts):
            continue
        source = document.read_text(encoding="utf-8")
        for raw in LINK.findall(source):
            target = raw.strip().strip("<>").split()[0]
            if not target or target.startswith(("#", "http://", "https://", "mailto:")):
                continue
            path = unquote(target.split("#", 1)[0])
            resolved = (document.parent / path).resolve()
            try:
                resolved.relative_to(ROOT)
            except ValueError:
                failures.append(f"{document.relative_to(ROOT)}: 链接越出仓库: {target}")
                continue
            if not resolved.exists():
                failures.append(f"{document.relative_to(ROOT)}: 不存在: {target}")
    if failures:
        print("\n".join(failures))
        return 1
    print("Markdown 相对链接检查通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
