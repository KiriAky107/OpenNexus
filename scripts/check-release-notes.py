"""Check the current version's tracked bilingual release notes before publishing."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import tomllib

ROOT = Path(__file__).resolve().parents[1]


def validate(notes: str, version: str) -> None:
    assert notes.startswith(f'# OpenNexus {version}\n'), 'Release title/version mismatch'
    assert '## 更新内容\n' in notes and '## English\n' in notes, 'Chinese and English release sections are required'
    chinese = notes.split('## 更新内容\n', 1)[1].split('\n## ', 1)[0]
    english = notes.split('## English\n', 1)[1]
    assert len(re.findall(r'[\u4e00-\u9fff]', chinese)) >= 30, 'Chinese release details are missing'
    assert len(re.findall(r'\b[A-Za-z]+\b', english)) >= 60, 'English release details are missing'
    assert len(re.findall(r'^- ', chinese, re.M)) == len(re.findall(r'^- ', english, re.M)) > 0, 'Chinese and English change lists must match'
    prefix = f'https://github.com/KiriAky107/OpenNexus/releases/download/v{version}/'
    for name in [f'OpenNexus_{version}_x64-setup.exe', f'OpenNexus_{version}_source.zip', 'SHA256SUMS.txt']:
        assert prefix + name in notes, f'Missing download: {name}'
    assert not re.search(r'releases/(?:download|tag)/v(?!' + re.escape(version) + r'(?:/|\)))', notes), 'Stale release link'


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--notes', type=Path)
    args = parser.parse_args()
    version = json.loads((ROOT / 'frontend/package.json').read_text(encoding='utf-8'))['version']
    host = tomllib.loads((ROOT / 'frontend/src-tauri/Cargo.toml').read_text(encoding='utf-8'))['package']['version']
    core = tomllib.loads((ROOT / 'backend/pyproject.toml').read_text(encoding='utf-8'))['project']['version']
    tauri = json.loads((ROOT / 'frontend/src-tauri/tauri.conf.json').read_text(encoding='utf-8'))['version']
    assert version == host == core == tauri, 'Frontend, Host, Core and package versions must match'
    notes = args.notes or ROOT / '.github/release-notes' / f'{version}.md'
    validate(notes.read_text(encoding='utf-8'), version)
    print(f'PASS: {version} bilingual notes, versions and downloads')


if __name__ == '__main__':
    main()
