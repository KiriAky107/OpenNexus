"""Offline reference scoring. No inference, uploads or fabricated reference labels."""
from __future__ import annotations
import math
import unicodedata


def edit_distance(reference, hypothesis):
    if len(reference) * len(hypothesis) > 20_000_000:
        raise ValueError('Text comparison exceeds 20 million cells; score shorter annotated recordings separately')
    row = list(range(len(hypothesis) + 1))
    for i, a in enumerate(reference, 1):
        next_row = [i]
        for j, b in enumerate(hypothesis, 1):
            next_row.append(min(next_row[-1] + 1, row[j] + 1, row[j-1] + (a != b)))
        row = next_row
    return row[-1]


def validate_segments(items):
    if isinstance(items, dict):
        items = items.get('segments')
    if not isinstance(items, list) or len(items) > 10000:
        raise ValueError('segments must be an array with at most 10000 entries')
    items = [dict(item, start=item.get('start', item.get('start_time')), end=item.get('end', item.get('end_time'))) for item in items]
    for item in items:
        start, end = item['start'], item['end']
        if not all(isinstance(value, (int, float)) and math.isfinite(value) for value in (start, end)) or start < 0 or end <= start:
            raise ValueError('Each segment needs finite 0 <= start < end times in seconds')
        if not isinstance(item.get('text', ''), str):
            raise ValueError('Segment text must be a string')
    return sorted(items, key=lambda item: (item['start'], item['end']))


def speaker_score(reference, hypothesis):
    if not reference or any(not isinstance(item.get('speaker'), str) or not item['speaker'] for item in reference + hypothesis):
        return {'status': 'unavailable', 'reason': 'Reference and hypothesis speaker labels are required'}
    refs = sorted({item['speaker'] for item in reference})
    hyps = sorted({item['speaker'] for item in hypothesis})
    count = max(len(refs), len(hyps))
    if count > 12:
        raise ValueError('Speaker scoring supports at most 12 speaker IDs per recording')
    boundaries = sorted({item[key] for item in reference + hypothesis for key in ('start', 'end')})
    weights = [[0.0] * count for _ in range(count)]
    denominator = missed = false_alarm = common = 0.0
    for start, end in zip(boundaries, boundaries[1:]):
        r = {item['speaker'] for item in reference if item['start'] < end and item['end'] > start}
        h = {item['speaker'] for item in hypothesis if item['start'] < end and item['end'] > start}
        duration = end - start
        denominator += duration * len(r)
        missed += duration * max(0, len(r) - len(h))
        false_alarm += duration * max(0, len(h) - len(r))
        common += duration * min(len(r), len(h))
        for a in r:
            for b in h:
                weights[refs.index(a)][hyps.index(b)] += duration
    # Exact maximum-weight one-to-one mapping, padded with silent dummy speakers.
    dp = {0: 0.0}
    for index in range(count):
        next_dp = {}
        for mask, score in dp.items():
            for column in range(count):
                if not mask & (1 << column):
                    key = mask | (1 << column)
                    next_dp[key] = max(next_dp.get(key, -1), score + weights[index][column])
        dp = next_dp
    confusion = max(0.0, common - max(dp.values()))
    return {'status': 'scored', 'collar_seconds': 0, 'overlap_included': True,
            'reference_speaker_seconds': denominator, 'missed_seconds': missed,
            'false_alarm_seconds': false_alarm, 'confusion_seconds': confusion,
            'der': (missed + false_alarm + confusion) / denominator if denominator else None}


def score(reference, hypothesis):
    reference, hypothesis = validate_segments(reference), validate_segments(hypothesis)
    if not reference:
        raise ValueError('A non-empty human reference is required')
    texts = [' '.join(unicodedata.normalize('NFC', item.get('text', '')) for item in items) for items in (reference, hypothesis)]
    metrics = {}
    for name, units in [('cer', [[c for c in text if not c.isspace()] for text in texts]), ('wer', [text.split() for text in texts])]:
        expected, actual = units
        edits = edit_distance(expected, actual)
        metrics[name] = {'edits': edits, 'reference_units': len(expected), 'rate': edits / len(expected) if expected else None}
    return {'text': metrics, 'speaker': speaker_score(reference, hypothesis),
            'normalization': 'NFC; punctuation/case retained; CER ignores whitespace; WER uses whitespace tokens',
            'quality_gate': 'not_evaluated', 'reference_segments': len(reference), 'hypothesis_segments': len(hypothesis)}
