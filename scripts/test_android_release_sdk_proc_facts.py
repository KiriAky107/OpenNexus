"""Pure controlled proc-read facts; no host proc, SDK, files or process execution."""
import ast
from dataclasses import FrozenInstanceError
from pathlib import Path
import unittest

import android_release_sdk_proc_facts as facts


class ProcFacts(unittest.TestCase):
    def test_five_original_read_steps_only_for_exact_rejected_comparisons(self):
        self.assertEqual(facts.STEPS, ('DIRECTORY_BEFORE_STAT', 'FIRST_STAT_READ', 'EXECUTABLE_READLINK',
                                      'SECOND_STAT_READ', 'DIRECTORY_AFTER_STAT'))
        for step in facts.STEPS:
            observed = facts.capture(step, True, True)
            self.assertIs(type(observed), facts.MissingProcRead)
            value = facts.project(observed)
            self.assertTrue(facts.validate(value))
            self.assertEqual(value['read_step'], step)
            self.assertFalse(value['operation_returned'])
            self.assertFalse(value['current_process_identity_verified'])
            self.assertFalse(value['process_absence_verified'])
            for missing, exists in ((False, True), (True, False), (None, True), (True, None),
                                    (None, None), (1, True), (True, 1), ('true', True), (True, 'true')):
                with self.subTest(step=step, missing=missing, exists=exists):
                    self.assertIsNone(facts.capture(step, missing, exists))

    def test_frozen_fact_has_no_current_identity_or_private_payload_and_projection_is_separate(self):
        observed = facts.capture('EXECUTABLE_READLINK', True, True)
        with self.assertRaises(FrozenInstanceError):
            observed.read_step = 'private original error'
        self.assertFalse(hasattr(observed, '__dict__'))
        self.assertEqual(tuple(observed.__slots__), ('read_step',))
        value = facts.project(observed)
        value['read_step'] = 'private mutation'
        value['runtime_verified'] = True
        self.assertEqual(facts.project(observed)['read_step'], 'EXECUTABLE_READLINK')
        self.assertFalse(facts.project(observed)['runtime_verified'])
        self.assertFalse(facts.validate(value))
        self.assertIsNone(facts.project(facts.MissingProcRead('private source')))
        self.assertIsNone(facts.project(None))

    def test_closed_schema_flags_and_types_reject_partial_private_or_coercion(self):
        value = facts.project(facts.capture('FIRST_STAT_READ', True, True))
        for key in value:
            partial = dict(value)
            del partial[key]
            with self.subTest(missing=key):
                self.assertFalse(facts.validate(partial))
        for extra in ('pid', 'uid', 'host_boot_id', 'path', 'raw_stat', 'exe', 'error', 'sha256'):
            self.assertFalse(facts.validate(dict(value, **{extra: 'private'})))
        for key, expected in value.items():
            if type(expected) is bool:
                for invalid in (None, int(expected), not expected, 'false'):
                    self.assertFalse(facts.validate(dict(value, **{key: invalid})))
        for bad in (dict(value, schema=True), dict(value, schema=2), dict(value, read_step='first_stat_read'),
                    dict(value, read_step='FIRST_STAT_READ\n'), {}, None, [], True):
            self.assertFalse(facts.validate(bad))

    def test_unknown_objects_are_not_stringified_hashed_or_treated_as_bool(self):
        class Private:
            def __str__(self):
                raise AssertionError('Unknown private object was formatted')

            def __bool__(self):
                raise AssertionError('Unknown private object was coerced')

        unknown = Private()
        for step in (unknown, None, True, '', 'FIRST_STAT_READ extra', 'x' * 1000):
            self.assertIsNone(facts.capture(step, True, True))
        self.assertIsNone(facts.capture('FIRST_STAT_READ', unknown, True))
        self.assertIsNone(facts.capture('FIRST_STAT_READ', True, unknown))
        self.assertIsNone(facts.project(unknown))
        self.assertFalse(facts.validate(unknown))
        self.assertFalse(facts.validate(dict(facts.project(facts.capture('SECOND_STAT_READ', True, True)), read_step=unknown)))

    def test_component_has_no_IO_clock_command_environment_or_admission_entry(self):
        tree = ast.parse(Path(facts.__file__).read_text(encoding='utf-8'))
        imports = [(node.module, tuple(alias.name for alias in node.names)) for node in tree.body if isinstance(node, ast.ImportFrom)]
        self.assertEqual(imports, [('dataclasses', ('dataclass',))])
        self.assertFalse(any(isinstance(node, ast.Import) for node in ast.walk(tree)))
        forbidden = {'open', 'read', 'read_bytes', 'write', 'write_bytes', 'stat', 'readlink', 'exists',
                     'monotonic', 'sleep', 'run', 'Popen', 'getenv', 'getuid', 'hash', 'str'}
        calls = {node.func.id if isinstance(node.func, ast.Name) else node.func.attr for node in ast.walk(tree)
                 if isinstance(node, ast.Call) and isinstance(node.func, (ast.Name, ast.Attribute))}
        self.assertFalse(calls & forbidden)
        self.assertEqual({node.name for node in tree.body if isinstance(node, ast.FunctionDef)},
                         {'_valid_step', 'capture', 'project', 'validate'})


if __name__ == '__main__':
    unittest.main()
