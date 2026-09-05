"""Reproducible, explicit-file-list community package builder; standard library only."""
import hashlib
import json
import re
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PACKAGES = [
    ('plugin', 'markdown-workbench', ['plugin.yaml', 'commands.yaml', 'server.py', 'example.md', 'README.md'], []),
    ('skill', 'note-reviewer', ['skill.yaml', 'prompt.md', 'README.md'], ['markdown-workbench']),
]


def build(output: Path | None = None) -> dict:
    output = output or ROOT / 'dist'
    output.mkdir(parents=True, exist_ok=True)
    entries = []
    for kind, identity, files, dependencies in PACKAGES:
        source = ROOT / f'{kind}s' / identity
        manifest = (source / f'{kind}.yaml').read_text(encoding='utf-8')
        version = re.search(r'^version: (\d+\.\d+\.\d+)$', manifest, re.M)[1]
        path = output / f'{identity}-{version}.zip'
        with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as archive:
            for name in sorted(files):
                info = zipfile.ZipInfo(f'{identity}/{name}', date_time=(1980, 1, 1, 0, 0, 0))
                info.create_system = 3
                info.external_attr = 0o100644 << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                content = (source / name).read_text(encoding='utf-8').replace('\r\n', '\n').encode('utf-8')
                archive.writestr(info, content)
        data = path.read_bytes()
        entries.append({'id': identity, 'kind': kind, 'version': version, 'file': path.name,
                        'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest(),
                        'dependencies': dependencies, 'license': None, 'publication_status': 'local-preview'})
    catalog = {'schema_version': 1, 'packages': entries}
    (output / 'index.json').write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return catalog


if __name__ == '__main__':
    print(json.dumps(build(), ensure_ascii=False, indent=2))
