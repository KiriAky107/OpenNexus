"""Bounded portable ink extension. Unknown JSON Canvas fields remain untouched."""
import math
import re

KEY = 'opennexusHandwriting'


def valid(value):
    def number(v, low, high):
        try:
            return type(v) in (int, float) and math.isfinite(v) and low <= v <= high
        except OverflowError:
            return False
    if not isinstance(value, dict) or not number(value.get('version'), 1, 1):
        return False
    if (value.get('mode') not in ('ink', 'text') or value.get('paper') not in ('plain', 'ruled', 'grid')
            or not number(value.get('width'), 64, 4096) or not number(value.get('height'), 64, 4096)
            or not number(value.get('fontSize'), 12, 72) or not number(value.get('lineHeight'), 1, 3)
            or not number(value.get('spacing'), 0, 12) or not number(value.get('variation'), 0, .3)
            or not number(value.get('seed'), 0, 2147483647) or int(value['seed']) != value['seed']
            or not isinstance(value.get('strokes'), list) or len(value['strokes']) > 1000):
        return False
    ids, count = set(), 0
    for stroke in value['strokes']:
        if not isinstance(stroke, dict):
            return False
        sid, points = stroke.get('id'), stroke.get('points')
        if (not isinstance(sid, str) or not 1 <= len(sid.encode('utf-8')) <= 128 or sid in ids
                or not isinstance(stroke.get('color'), str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', stroke['color'])
                or not number(stroke.get('width'), .5, 24) or not isinstance(points, list) or not points):
            return False
        ids.add(sid)
        count += len(points)
        if count > 20000:
            return False
        for point in points:
            if (not isinstance(point, dict) or not number(point.get('x'), 0, value['width'])
                    or not number(point.get('y'), 0, value['height']) or not number(point.get('pressure'), 0, 1)):
                return False
    return value['mode'] != 'text' or not value['strokes']
