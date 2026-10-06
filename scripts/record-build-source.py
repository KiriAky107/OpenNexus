"""Persist a clean, fixed checkout's source bytes before compiling the package."""
import argparse
from pathlib import Path

import json
from release_plan import COMMIT, atomic_json, build_source, require

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--commit', required=True)
parser.add_argument('--output', type=Path)
parser.add_argument('--repository-dir', type=Path, default=Path(__file__).resolve().parents[1])
args = parser.parse_args()
require(COMMIT.fullmatch(args.commit), 'FIXED_BUILD_COMMIT_REQUIRED')
source = build_source(args.repository_dir, args.commit)
output = args.output or args.repository_dir/'.build/source-provenance'/f'{args.commit}.json'
require(not output.is_symlink(), 'BUILD_SOURCE_RECEIPT_LINK')
if output.exists():
    require(json.loads(output.read_text('utf-8')) == source, 'BUILD_SOURCE_RECEIPT_CHANGED')
else:
    atomic_json(output, source)
print('FIXED_BUILD_SOURCE_RECORDED '+args.commit+' '+str(len(source['files_sha256'])))
