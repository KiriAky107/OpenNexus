from pathlib import Path


LOCK_FILES = (
    Path("backend/uv.lock"),
    Path("server sync/uv.lock"),
    Path("community-server/uv.lock"),
)

REPLACEMENTS = {
    "https://pypi.org/simple": "https://mirrors.aliyun.com/pypi/simple",
    "https://files.pythonhosted.org/packages/": "https://mirrors.aliyun.com/pypi/packages/",
}


def main() -> None:
    for path in LOCK_FILES:
        if not path.is_file():
            continue
        content = path.read_text(encoding="utf-8")
        for source, mirror in REPLACEMENTS.items():
            content = content.replace(source, mirror)
        path.write_text(content, encoding="utf-8", newline="\n")
        print(f"已切换锁文件下载源：{path}")


if __name__ == "__main__":
    main()
