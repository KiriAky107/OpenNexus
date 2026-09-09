"""上传到 AI Core 主机的包的有限 ZIP 提取。"""
from __future__ import annotations

import io
import re
import shutil
import stat
import tempfile
import zipfile
import zlib
from pathlib import Path
from collections.abc import Callable
from typing import TypeVar

from app.errors import ApiError
from app.extensions.errors import ExtensionError

MAX_ZIP_BYTES = 10 * 1024 * 1024
MAX_EXPANDED_BYTES = 50 * 1024 * 1024
MAX_ENTRIES = 2048
T = TypeVar('T')


def invalid(message: str) -> ApiError:
    return ApiError(422, 'EXTENSION_ZIP_INVALID', message)


def install_zip(data: bytes, kind: str, storage: Path, install: Callable[[Path], T], *, managed_install: Callable[[Path, Path], T] | None = None) -> T:
    if len(data) > MAX_ZIP_BYTES:
        raise ApiError(413, 'EXTENSION_ZIP_TOO_LARGE', 'ZIP 文件不能超过 10 MiB。')
    if kind not in ('skill', 'plugin'):
        raise ValueError('Unknown extension kind')
    storage.mkdir(parents=True, exist_ok=True)
    # 保留成功提取：Plugin 命令和资源使用此目录。
    destination = Path(tempfile.mkdtemp(prefix=f'{kind}-', dir=storage))
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            if not entries or len(entries) > MAX_ENTRIES:
                raise invalid('ZIP 为空或文件条目超过 2048 个。')
            seen: set[str] = set()
            spellings: dict[str, str] = {}
            total = 0
            for entry in entries:
                name = entry.filename.rstrip('/')
                parts = name.split('/')
                if (entry.orig_filename != entry.filename or '\\' in name
                    or any(not p or p in ('.', '..') or any(c in p for c in ':*?<>|"') or p.endswith((' ', '.'))
                           or any(ord(c) < 32 for c in p)
                           or re.match(r'^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)', p, re.I)
                           for p in parts)):
                    raise invalid('ZIP 包含不安全的文件路径。')
                mode = stat.S_IFMT(entry.external_attr >> 16)
                if mode not in (0, stat.S_IFREG, stat.S_IFDIR) or entry.flag_bits & 1:
                    raise invalid('ZIP 不支持链接、特殊文件或加密条目。')
                if entry.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
                    raise invalid('ZIP 仅支持 stored/deflate 压缩。')
                key = name.casefold()
                if key in seen:
                    raise invalid('ZIP 包含重复或大小写冲突的路径。')
                seen.add(key)
                for index in range(1, len(parts) + 1):
                    prefix = '/'.join(parts[:index])
                    if spellings.setdefault(prefix.casefold(), prefix) != prefix:
                        raise invalid('ZIP 包含大小写冲突的目录。')
                total += entry.file_size
                if total > MAX_EXPANDED_BYTES:
                    raise ApiError(413, 'EXTENSION_ZIP_TOO_LARGE', 'ZIP 解压后不能超过 50 MiB。')
                target = destination.joinpath(*parts)
                if not target.resolve().is_relative_to(destination.resolve()):
                    raise invalid('ZIP 路径超出包目录。')
            written = 0
            for entry in entries:
                target = destination.joinpath(*entry.filename.rstrip('/').split('/'))
                if entry.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(entry) as source, target.open('xb') as output:
                    while chunk := source.read(64 * 1024):
                        written += len(chunk)
                        if written > MAX_EXPANDED_BYTES:
                            raise ApiError(413, 'EXTENSION_ZIP_TOO_LARGE', 'ZIP 解压后不能超过 50 MiB。')
                        output.write(chunk)
            manifest = f'{kind}.yaml'
            root = destination
            if not (root / manifest).is_file():
                children = list(root.iterdir())
                if len(children) != 1 or not children[0].is_dir() or not (children[0] / manifest).is_file():
                    raise invalid(f'ZIP 根目录或唯一顶层文件夹中须包含 {manifest}。')
                root = children[0]
            return managed_install(root, destination) if managed_install else install(root)
    except BaseException as error:
        shutil.rmtree(destination)
        if isinstance(error, ExtensionError):
            raise
        if isinstance(error, (zipfile.BadZipFile, OSError, RuntimeError, NotImplementedError, zlib.error, EOFError, UnicodeError)):
            raise invalid('ZIP 损坏、路径冲突或无法解压。') from error
        raise
