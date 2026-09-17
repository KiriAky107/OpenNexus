"""可重复的、显式文件列表社区包构建器；仅标准库。"""
import hashlib
import json
import re
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PACKAGES = [
    ('plugin', 'markdown-workbench', ['plugin.yaml', 'commands.yaml', 'markdown-workbench.exe', 'example.md', 'README.md'], []),
    ('plugin', 'study-plan-kit', ['plugin.yaml', 'study-plan-kit.exe', 'README.md'], []),
    ('skill', 'note-reviewer', ['skill.yaml', 'prompt.md', 'README.md'], ['markdown-workbench']),
    ('skill', 'course-note-rewriter', ['skill.yaml', 'prompt.md', 'README.md'], ['markdown-workbench']),
    ('skill', 'adaptive-study-coach', ['skill.yaml', 'prompt.md', 'README.md'], ['study-plan-kit']),
]


def build(output: Path | None = None) -> dict:
    output = output or ROOT / 'dist'
    output.mkdir(parents=True, exist_ok=True)
    entries = []
    for kind, identity, files, dependencies in PACKAGES:
        source = ROOT / f'{kind}s' / identity
        generated: dict[str, bytes] = {}
        executable_names = [name for name in files if name.endswith('.exe')]
        if executable_names:
            with tempfile.TemporaryDirectory(prefix='opennexus-community-') as directory:
                if len(executable_names) != 1:
                    raise RuntimeError(f'{identity} must declare exactly one executable')
                executable = Path(directory) / executable_names[0]
                rustc_command = [
                    'rustc', '--edition=2021', '--crate-name', identity.replace('-', '_'),
                    '-C', f'metadata=opennexus-community-{identity}-v1', '-C', 'opt-level=s',
                    '-C', 'strip=symbols',
                ]
                if sys.platform == 'win32':
                    rustc_command.extend(['-C', 'link-arg=-Wl,--no-insert-timestamp'])
                rustc_command.extend([str(source / 'server.rs'), '-o', str(executable)])
                subprocess.run(rustc_command, check=True)
                generated[executable.name] = executable.read_bytes()
        manifest = (source / f'{kind}.yaml').read_text(encoding='utf-8')
        version = re.search(r'^version: (\d+\.\d+\.\d+)$', manifest, re.M)[1]
        path = output / f'{identity}-{version}.zip'
        with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as archive:
            for name in sorted(files):
                info = zipfile.ZipInfo(f'{identity}/{name}', date_time=(1980, 1, 1, 0, 0, 0))
                info.create_system = 3
                info.external_attr = (0o100755 if name.endswith('.exe') else 0o100644) << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                content = generated.get(name)
                if content is None:
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
