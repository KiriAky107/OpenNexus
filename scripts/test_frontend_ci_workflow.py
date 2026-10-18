"""Keep renderer-only validation separate from Native clients and fixed APKs."""
import ast
from pathlib import Path
import re
import unittest


class FrontendScope(unittest.TestCase):
    def setUp(self):
        self.source = (Path(__file__).resolve().parents[1] / '.github/workflows/ci.yml').read_text('utf-8')
        pairs = re.findall(r'^  ([a-z-]+):\n    if: ([^\n]+)$', self.source, re.M)
        self.assertEqual(len(pairs), len(dict(pairs)))
        self.jobs = dict(pairs)
        self.assertEqual(set(self.jobs), {'docs', 'backend', 'frontend', 'rust', 'windows-host',
            'windows-records', 'sync-http', 'sync-http-android'})

    def selected(self, scope, event='workflow_dispatch'):
        result = set()
        for name, condition in self.jobs.items():
            expression = condition.removeprefix('${{').removesuffix('}}').strip()
            expression = expression.replace('github.event_name', repr(event)).replace('inputs.check_scope', repr(scope))
            tree = ast.parse(expression.replace('&&', ' and ').replace('||', ' or '), mode='eval')
            self.assertTrue(all(isinstance(node, (ast.Expression, ast.BoolOp, ast.Compare, ast.Constant,
                ast.And, ast.Or, ast.Eq, ast.NotEq)) for node in ast.walk(tree)))
            if eval(compile(tree, 'scoped-ci-condition', 'eval'), {'__builtins__': {}}): result.add(name)
        return result

    def test_frontend_only_admits_the_renderer_job(self):
        self.assertEqual(self.selected('frontend'), {'frontend'})
        self.assertRegex(self.source, r'options: \[[^\]\n]*\bfrontend\b[^\]\n]*\]')

    def test_old_manual_and_main_job_boundaries_are_preserved(self):
        original = set(self.jobs) - {'windows-records'}
        self.assertEqual(self.selected('all'), original)
        self.assertEqual(self.selected('all', 'push'), original)
        self.assertEqual(self.selected('all', 'pull_request'), original)
        self.assertEqual(self.selected('sync-http'), {'sync-http', 'sync-http-android'})
        self.assertEqual(self.selected('windows-records'), {'windows-records'})

    def test_shared_renderer_scope_checks_both_desktop_and_mobile_assets(self):
        frontend = self.source.split('\n  frontend:\n', 1)[1].split('\n  rust:\n', 1)[0]
        for command in ('pnpm test --maxWorkers=2', 'pnpm type-check', 'pnpm build', 'pnpm mobile:build'):
            self.assertIn('run: ' + command, frontend)
        self.assertIn("if: github.event_name == 'workflow_dispatch' && inputs.check_scope == 'frontend'", frontend)
        self.assertNotRegex(frontend, r'\b(cargo|gradle|emulator|tauri build)\b')


if __name__ == '__main__': unittest.main()
