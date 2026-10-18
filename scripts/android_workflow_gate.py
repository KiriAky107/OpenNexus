"""Pure literal workflow-gate evaluation, without device or desktop dependencies."""
import ast


def admitted(expression, scope, *, reuse='', event='workflow_dispatch', build='success'):
    """Evaluate only the literal boolean subset used by the Android job gates."""
    value = expression.removeprefix('${{').removesuffix('}}').strip()
    for field, replacement in [('github.event_name', repr(event)), ('inputs.runtime_scope', repr(scope)),
                               ('inputs.apk_run_id', repr(reuse)), ('needs.debug-apk.result', repr(build)),
                               ('!cancelled()', 'True'), ('always()', 'True')]:
        value = value.replace(field, replacement)
    value = value.replace('&&', ' and ').replace('||', ' or ')
    tree = ast.parse(value, mode='eval')
    if not all(isinstance(node, (ast.Expression, ast.BoolOp, ast.Compare, ast.Constant,
                                 ast.And, ast.Or, ast.Eq, ast.NotEq)) for node in ast.walk(tree)):
        raise ValueError('Unsupported job condition')
    return eval(compile(tree, 'fixed-workflow-job-gate', 'eval'), {'__builtins__': {}})
