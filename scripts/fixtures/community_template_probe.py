"""Signed template fixture: selected JSON/CSV inputs, independently run output."""
import csv
import io
import json
from pathlib import Path
import sys

assert sys.flags.isolated == 1 and len(sys.argv) == 1
source = Path(__file__).parent
settings = json.loads((source / 'inputs/data.json').read_text(encoding='utf-8'))
rows = list(csv.DictReader(io.StringIO((source / 'inputs/表格.csv').read_text(encoding='utf-8'))))
assert rows[0]['name'] == '中文' and settings['value'] == 2
assert not (source / 'not-selected.py').exists()
try:
    (source / 'inputs/data.json').write_text('{}', encoding='utf-8')
except PermissionError:
    pass
else:
    raise RuntimeError('selected inputs must remain read-only')
total = sum(int(row['value']) for row in rows) * settings['value']
report = f'# 课程成果\r\n总计：{total}\r\n'
Path('报告.md').write_bytes(report.encode('utf-8'))
Path('result.json').write_bytes(json.dumps({'total': total}, ensure_ascii=False).encode('utf-8'))
print(f'TEMPLATE_RUN:中文:{total}:{sys.version.split()[0]}', flush=True)
