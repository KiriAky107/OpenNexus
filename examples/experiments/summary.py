"""Run with the selected inputs beside this source; outputs go to private cwd."""
import csv
import json
import math
from pathlib import Path
from statistics import fmean


def main():
    inputs = Path(__file__).parent / 'inputs'
    settings = json.loads((inputs / 'settings.json').read_text(encoding='utf-8'))
    with (inputs / 'data.csv').open(encoding='utf-8-sig', newline='') as stream:
        rows = list(csv.DictReader(stream))
    values = [float(row['value']) for row in rows]
    if not values or not all(math.isfinite(value) for value in values):
        raise ValueError('Select a nonempty CSV with finite numeric values.')
    summary = {'count': len(values), 'mean': fmean(values), 'minimum': min(values), 'maximum': max(values)}
    output = Path('results')
    output.mkdir(exist_ok=True)
    (output / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    with (output / 'summary.csv').open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=summary)
        writer.writeheader()
        writer.writerow(summary)
    title = str(settings.get('title', 'Experiment summary')).replace('\n', ' ').replace('\r', ' ')
    report = f'# {title}\n\n' + '\n'.join(f'- {name}: {value}' for name, value in summary.items()) + '\n'
    (output / 'report.md').write_text(report, encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == '__main__':
    main()
