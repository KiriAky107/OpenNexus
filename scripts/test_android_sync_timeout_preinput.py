import copy
import hashlib
import json
import os
from pathlib import Path
import unittest

from android_sync_timeout_preinput import canonical_parent, canonical_ready, decode_wire, validate_wire


class Poison:
    def __str__(self): raise AssertionError('Unknown stringified')
    def __repr__(self): raise AssertionError('Unknown formatted')
    def __bool__(self): raise AssertionError('Unknown coerced')
    def __eq__(self, _): raise AssertionError('Unknown compared')
    def __hash__(self): raise AssertionError('Unknown hashed')


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()


class PreinputMetadataCheck(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # One synthetic JVM output in the outer owned task root; never a device file.
        cls.fixture = json.loads(Path(os.environ['ONX_PREINPUT_METADATA_VECTORS']).read_bytes())
        assert cls.fixture['passed'] is True and cls.fixture['actual_UI_or_Native_verified'] is False
        cls.parent = cls.fixture['parent']
        cls.vectors = cls.fixture['vectors']

    def test_cross_language_canonical_and_closed_projection(self):
        self.assertEqual(canonical_parent(self.parent).hex(), self.fixture['parent_canonical_hex'])
        self.assertEqual(canonical_ready(self.vectors[0]['original_ready'], self.parent).hex(), self.fixture['ready_canonical_hex'])
        for wire in self.vectors:
            uid = wire['original_ready']['uid']
            projected = decode_wire(encoded(wire), self.parent, uid)
            self.assertEqual(projected, wire)
            e = projected['preinput_current_fit']
            self.assertEqual(e['parent_metadata_canonical_sha256'], hashlib.sha256(encoded(self.parent)).hexdigest())
            self.assertEqual(e['ready_metadata_canonical_sha256'], hashlib.sha256(encoded(wire['original_ready'])).hexdigest())
            for key in ('intent_written', 'attempt_written'):
                if e[key] is not None:
                    self.assertEqual(e[key]['raw_sha256'], hashlib.sha256(encoded(e[key]['metadata'])).hexdigest())
            self.assertIs(e['future_warning_visibility_guaranteed'], False)
            self.assertIs(e['network_admission'], False)
            self.assertIs(e['native_timeout_verified'], False)
            self.assertLessEqual(len(encoded(wire)), 4096)

    def test_types_scope_hashes_and_reported_write_refusals(self):
        for template in self.vectors:
            uid = template['original_ready']['uid']
            for field in ('schema', 'uid', 'window', 'x', 'y', 'elapsed_ms'):
                for bad in (True, 1.0, None, '1'):
                    wire = copy.deepcopy(template); wire['original_ready'][field] = bad
                    self.assertIsNone(validate_wire(wire, self.parent, uid))
            for field in template['preinput_current_fit']:
                if field in ('intent_written', 'attempt_written', 'reveal_returned'):
                    continue
                wire = copy.deepcopy(template); old = wire['preinput_current_fit'][field]
                wire['preinput_current_fit'][field] = (wire['preinput_current_fit']['started_uptime_ms'] + 5001
                    if field == 'final_sample_uptime_ms' else (not old) if type(old) is bool
                    else (old + 1 if type(old) is int else 'private/unknown'))
                self.assertIsNone(validate_wire(wire, self.parent, uid))
            for field in ('parent_metadata_canonical_sha256', 'ready_metadata_canonical_sha256'):
                wire = copy.deepcopy(template); wire['preinput_current_fit'][field] = '0' * 64
                self.assertIsNone(validate_wire(wire, self.parent, uid))
            for key in ('intent_written', 'attempt_written'):
                if template['preinput_current_fit'][key] is None:
                    continue
                for field, bad in [('writer_returned', False), ('writer_returned', None), ('raw_sha256', '0' * 64)]:
                    wire = copy.deepcopy(template); wire['preinput_current_fit'][key][field] = bad
                    self.assertIsNone(validate_wire(wire, self.parent, uid))
            self.assertIsNone(validate_wire(template, self.parent, uid + 1))
        for bad in (False, True, 0, {}):
            wire = copy.deepcopy(self.vectors[0]); wire['preinput_current_fit']['reveal_returned'] = bad
            self.assertIsNone(validate_wire(wire, self.parent, 12001))
        for bad in (False, None, 1):
            wire = copy.deepcopy(self.vectors[1]); wire['preinput_current_fit']['reveal_returned'] = bad
            self.assertIsNone(validate_wire(wire, self.parent, 12001))
        parent = dict(self.parent, input_intent_id=self.parent['parent_intent_id'])
        self.assertIsNone(canonical_parent(parent))
        self.assertIsNone(validate_wire(self.vectors[0], parent, 12001))

    def test_partial_duplicate_encoding_and_entire_buffer_limit(self):
        raw = encoded(self.vectors[0])
        for bad in (b'', raw[:len(raw)//2], raw[:-1], b'not json', raw + b' extra',
                    raw.replace(b'"schema":2', b'"schema":2,"schema":2', 1),
                    raw.replace(b'"uid":12001', b'"uid":12001,"uid":12001', 1),
                    b'\xef\xbb\xbf' + raw, raw.decode().encode('utf-16'), b'\xff' + raw):
            self.assertIsNone(decode_wire(bad, self.parent, 12001))
        self.assertEqual(decode_wire(raw + b' ' * (4096 - len(raw)), self.parent, 12001), self.vectors[0])
        self.assertIsNone(decode_wire(raw + b' ' * (4097 - len(raw)), self.parent, 12001))
        self.assertIsNone(decode_wire(bytearray(raw), self.parent, 12001))
        self.assertIsNone(decode_wire(encoded(self.vectors[0]['original_ready']), self.parent, 12001))

    def test_unknown_values_not_formatted_and_output_has_no_alias(self):
        def paths(value, prefix=()):
            if type(value) is dict:
                for key, item in value.items():
                    yield prefix + (key,)
                    yield from paths(item, prefix + (key,))
            elif type(value) is list:
                for key, item in enumerate(value):
                    yield prefix + (key,)
                    yield from paths(item, prefix + (key,))
        for path in paths(self.vectors[1]):
            wire = copy.deepcopy(self.vectors[1]); target = wire
            for part in path[:-1]: target = target[part]
            target[path[-1]] = Poison()
            self.assertIsNone(validate_wire(wire, self.parent, 12001))
        self.assertIsNone(canonical_parent(Poison()))
        self.assertIsNone(canonical_ready(Poison(), self.parent))
        self.assertIsNone(decode_wire(Poison(), self.parent, 12001))
        self.assertIsNone(validate_wire(self.vectors[0], self.parent, Poison()))
        original = copy.deepcopy(self.vectors[1]); result = validate_wire(original, self.parent, 12001)
        original['original_ready']['original'][0] = 999
        original['preinput_current_fit']['intent_written']['metadata']['uid'] = 999
        self.assertEqual(result, self.vectors[1])
        result['original_ready']['visible'][0] = 999
        result['preinput_current_fit']['attempt_written']['metadata']['uid'] = 999
        self.assertEqual(self.vectors[1]['original_ready']['visible'][0], 10)

    def test_private_extra_missing_policy_and_old_attempt_scope_refused(self):
        template = self.vectors[1]
        objects = [(), ('original_ready',), ('preinput_current_fit',),
                   ('preinput_current_fit', 'intent_written'), ('preinput_current_fit', 'intent_written', 'metadata'),
                   ('preinput_current_fit', 'attempt_written'), ('preinput_current_fit', 'attempt_written', 'metadata')]
        for path in objects:
            for extra in (False, True):
                wire = copy.deepcopy(template); target = wire
                for key in path: target = target[key]
                if extra: target['private_unknown'] = 'private/provider/path' * 500
                else: del target[next(iter(target))]
                self.assertIsNone(validate_wire(wire, self.parent, 12001))
        for field, bad in [('intent_raw_sha256', '0' * 64), ('attempt', True), ('action', 'CLICK'), ('uid', 12002)]:
            wire = copy.deepcopy(template); wire['preinput_current_fit']['attempt_written']['metadata'][field] = bad
            self.assertIsNone(validate_wire(wire, self.parent, 12001))
        wire = copy.deepcopy(template); wire['preinput_current_fit']['attempt_written'] = None
        self.assertIsNone(validate_wire(wire, self.parent, 12001))


if __name__ == '__main__':
    unittest.main()
