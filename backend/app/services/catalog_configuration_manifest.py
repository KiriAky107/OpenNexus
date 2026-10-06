"""Shared declarative shape checks; validation never applies or starts a target."""
import re

SECRET_KEY = re.compile(r'(api[_-]?key|token|password|secret|authorization|cookie)', re.I)
RUNTIME_RANGES = {'cpu_threads': (1, 32), 'memory_limit_mb': (1024, 131072),
                  'gpu_memory_limit_mb': (512, 65536), 'timeout_seconds': (30, 14400)}


def validate_configuration(kind, value):
    if not isinstance(value, dict):
        raise ValueError('Configuration manifest must be an object')
    if 'schema_version' in value and (type(value['schema_version']) is not int or value['schema_version'] != 1):
        raise ValueError('Unsupported configuration schema')
    permissions = value.get('permissions', [])
    if (not isinstance(permissions, list) or len(permissions) > 64
            or not all(isinstance(key, str) for key in permissions)
            or len(set(permissions)) != len(permissions)):
        raise ValueError('Invalid permission declarations')
    if kind == 'mcp':
        transport = value.get('transport')
        if not isinstance(transport, str) or transport not in {'stdio', 'streamable_http', 'sse'}:
            raise ValueError('Unsupported MCP transport')
        if transport == 'stdio' and 'args' not in value:
            raise ValueError('MCP stdio requires an argument array')
        if 'args' in value and (not isinstance(value['args'], list) or not all(isinstance(arg, str) for arg in value['args'])):
            raise ValueError('MCP arguments must be strings')
        if 'command' in value and (not isinstance(value['command'], str) or not value['command'].strip()):
            raise ValueError('Invalid MCP command')
        if 'name' in value and (not isinstance(value['name'], str) or not 1 <= len(value['name'].strip()) <= 80):
            raise ValueError('Invalid MCP name')
        if set(value) & {'enabled', 'approved_digest', 'tested_digest', 'version'}:
            raise ValueError('Package data cannot grant target lifecycle approval')
        for field in ('environment', 'headers'):
            mapping = value.get(field, {})
            if (not isinstance(mapping, dict) or not all(isinstance(v, str) for v in mapping.values())
                    or any(SECRET_KEY.search(key) for key in mapping)):
                raise ValueError('Use secret key declarations instead of plain credentials')
        for field in ('secret_environment_keys', 'secret_header_keys'):
            keys = value.get(field, [])
            if (not isinstance(keys, list) or len(keys) > 64 or not all(isinstance(key, str) for key in keys)
                    or len(set(keys)) != len(keys)):
                raise ValueError('Invalid secret key declarations')
        return
    if kind != 'model':
        raise ValueError('Unsupported configuration kind')
    if not all(isinstance(value.get(key), str) and value[key].strip() for key in ('source', 'revision', 'license')):
        raise ValueError('Model source, revision and license are required')
    platforms = value.get('verified_platforms')
    if (not isinstance(value.get('resources'), dict) or not value['resources']
            or not isinstance(platforms, list) or not platforms or not all(isinstance(item, str) and item.strip() for item in platforms)):
        raise ValueError('Model resources and verified platforms are required')
    if 'model_key' not in value and 'runtime_config' not in value:
        # Earlier metadata-only plans stay readable. The target application
        # service independently decides whether a concrete target is supported.
        return
    key, runtime = value.get('model_key'), value.get('runtime_config')
    if (not isinstance(key, str) or not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,63}', key)
            or not isinstance(runtime, dict)
            or set(runtime) - {*RUNTIME_RANGES, 'device', 'embedding_model'}
            or runtime.get('embedding_model') != key):
        raise ValueError('Model runtime must bind its declared model key')
    if 'device' in runtime and runtime['device'] not in ('cpu', 'cuda'):
        raise ValueError('Unsupported model device')
    for field, (minimum, maximum) in RUNTIME_RANGES.items():
        if field in runtime and (type(runtime[field]) is not int or not minimum <= runtime[field] <= maximum):
            raise ValueError('Invalid model runtime budget')
