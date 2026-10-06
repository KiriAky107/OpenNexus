"""Prepare fixed-commit release files locally. Does not contact GitHub."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

from release_plan import COMMIT, PRIVATE, VERSION, Plan, ReleaseError, atomic_json, check_local_source, digest, fixed_archive, require, text_digest


def git(root, *args):
    return subprocess.check_output(['git', *args], cwd=root)


def prepare(root, output, *, repository, product, version, commit, notes, verification, installer=None, deployment=None, original=None):
    root, output = Path(root).resolve(), Path(output).resolve()
    require(COMMIT.fullmatch(commit) and VERSION.fullmatch(version), 'FIXED_VERSION_AND_COMMIT_REQUIRED')
    require(re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository), 'REPOSITORY_INVALID')
    require(isinstance(product, str) and 0 < len(product) <= 100 and '\n' not in product and '\r' not in product, 'PRODUCT_INVALID')
    require(git(root, 'rev-parse', 'HEAD').decode().strip() == commit and not git(root, 'status', '--porcelain').strip(), 'FIXED_CLEAN_CHECKOUT_REQUIRED')
    tracked = git(root, 'ls-tree', '-r', '--name-only', '-z', commit).decode().split('\0')
    require(not any(name.casefold().startswith(PRIVATE) for name in tracked), 'PRIVATE_SOURCE_TRACKED')
    proof = json.loads(Path(verification).read_text('utf-8'))
    require(proof.get('source_commit') == commit and proof.get('source_clean') is True, 'BUILD_SOURCE_MISMATCH')
    output.mkdir(parents=True, exist_ok=False)
    # Output is intentionally new. Existing releases and backups are never replaced.
    assets = []

    def asset(file, kind):
        assets.append({'name': file.name, 'file': file.name, 'kind': kind, 'bytes': file.stat().st_size, 'sha256': digest(file)})

    source = output/f'{repository.split("/")[1]}_{version}_source.zip'
    fixed_archive(root, commit, source)
    asset(source, 'source')
    if installer:
        require(product == 'OpenNexus' and proof.get('installer_sha256') == digest(installer), 'INSTALLER_SOURCE_MISMATCH')
        target = output/f'OpenNexus_{version}_x64-setup.exe'
        shutil.copyfile(installer, target)
        assets.insert(0, {'name': target.name, 'file': target.name, 'kind': 'installer', 'bytes': target.stat().st_size, 'sha256': digest(target)})
    if deployment:
        require(product != 'OpenNexus', 'DESKTOP_DEPLOYMENT_NOT_EXPECTED')
        target = output/f'{repository.split("/")[1]}_{version}_deploy.zip'
        shutil.copyfile(deployment, target)
        asset(target, 'deployment')
    require(product != 'OpenNexus' or installer is not None, 'DESKTOP_INSTALLER_REQUIRED')
    require(product == 'OpenNexus' or deployment is not None, 'SERVICE_DEPLOYMENT_REQUIRED')
    sums = output/'SHA256SUMS.txt'
    sums.write_text(''.join(f'{entry["sha256"]}  {entry["name"]}\n' for entry in assets), encoding='utf-8', newline='\n')
    asset(sums, 'checksums')
    release_notes = output/'RELEASE-NOTES.md'
    release_notes.write_text(Path(notes).read_text('utf-8').replace('\r\n', '\n'), encoding='utf-8', newline='\n')
    receipt = output/'verification.json'
    shutil.copyfile(verification, receipt)
    data = {'schema': 1, 'repository': repository, 'product': product, 'version': version, 'tag': 'v'+version, 'commit': commit,
            'assets': assets, 'notes_file': release_notes.name, 'notes_sha256': text_digest(release_notes.read_text('utf-8')),
            'verification': {'file': receipt.name, 'sha256': digest(receipt)}, 'original': original}
    path = output/'release-plan.json'
    atomic_json(path, data)
    plan = Plan(path)
    check_local_source(plan, root)
    if product == 'OpenNexus':
        # Keep the established title, bilingual list and exact download checks.
        subprocess.run([sys.executable, str(root/'scripts/check-release-notes.py'), '--notes', str(release_notes)], cwd=root, check=True)
    return {'plan': str(path), 'commit': commit, 'assets': [{'name': x['name'], 'sha256': x['sha256']} for x in assets], 'remote_modified': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repository-dir', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--repository', required=True, help='GitHub owner/repository')
    parser.add_argument('--product', required=True)
    parser.add_argument('--version', required=True)
    parser.add_argument('--commit', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--notes', type=Path, required=True)
    parser.add_argument('--verification', type=Path, required=True)
    parser.add_argument('--installer', type=Path)
    parser.add_argument('--deployment', type=Path)
    parser.add_argument('--original', type=Path, help='Explicit original identity snapshot for an authorized same-version replacement')
    args = parser.parse_args()
    try:
        result = prepare(args.repository_dir, args.output, repository=args.repository, product=args.product, version=args.version,
                         commit=args.commit, notes=args.notes, verification=args.verification, installer=args.installer,
                         deployment=args.deployment, original=json.loads(args.original.read_text('utf-8')) if args.original else None)
        print(json.dumps(result, indent=2))
        return 0
    except ReleaseError as error:
        print(str(error), file=sys.stderr)
        return 2
    except Exception as error:
        print('RELEASE_PREPARATION_FAILED_'+type(error).__name__, file=sys.stderr)
        return 3


if __name__ == '__main__':
    raise SystemExit(main())
