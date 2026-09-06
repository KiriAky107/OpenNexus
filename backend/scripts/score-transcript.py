"""Score authorized reference/hypothesis JSON segment arrays without a model or network."""
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.acceptance import score

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('reference', type=Path)
    parser.add_argument('hypothesis', type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    result = score(json.loads(args.reference.read_text(encoding='utf-8-sig')), json.loads(args.hypothesis.read_text(encoding='utf-8-sig')))
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
