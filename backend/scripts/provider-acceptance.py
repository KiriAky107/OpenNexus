"""Explicit, bounded connection smoke against an already configured local Provider.

Defaults to a plan. --execute performs one test request, never reads credentials.
The output deliberately keeps untested protocol scenarios pending.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

SCENARIOS = ['model_discovery', 'tool_roundtrip', 'stream_reasoning_and_content',
             'stream_cancel', 'cache_hit_and_miss', 'context_limit', 'context_compression']


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-url', default='http://127.0.0.1:8000')
    parser.add_argument('--provider', required=True)
    parser.add_argument('--model', required=True)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--execute', action='store_true', help='Perform one provider connection test; may incur provider charges')
    args = parser.parse_args()
    target = urlparse(args.base_url)
    if target.scheme != 'http' or target.hostname not in ('127.0.0.1', 'localhost', '::1') or target.username or target.password or target.query or target.fragment:
        parser.error('Use a local HTTP AI Core address without credentials or query parameters')
    result = {'date': datetime.now(timezone.utc).isoformat(), 'provider': args.provider, 'model': args.model,
              'max_test_requests': 1, 'connection': 'pending',
              'scenarios': {name: 'pending' for name in SCENARIOS}, 'overall': 'not_accepted'}
    if args.execute:
        body = json.dumps({'provider_id': args.provider, 'model': args.model}).encode()
        request = Request(args.base_url.rstrip('/') + '/api/providers/test', data=body, headers={'Content-Type': 'application/json'}, method='POST')
        try:
            with urlopen(request, timeout=60) as response:
                payload = json.load(response)
            result['connection'] = 'passed' if payload.get('success') is True else 'failed'
            result['latency_ms'] = payload.get('latency_ms')
        except HTTPError as error:
            result['connection'] = 'failed'
            result['http_status'] = error.code  # Do not persist remote error bodies or headers.
        except (URLError, TimeoutError, ValueError):
            result['connection'] = 'unavailable'
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
