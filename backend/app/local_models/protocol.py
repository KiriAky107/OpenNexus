"""Bound embedding result frames so large notes do not exceed pipe line limits."""
import json


def response_lines(response, operation):
    if operation == 'embedding' and 'result' in response and 'error_code' not in response:
        vectors = response['result']
        for offset in range(0, len(vectors), 128):
            yield json.dumps({'embedding_offset': offset, 'embedding_chunk': vectors[offset:offset + 128]}, allow_nan=False) + '\n'
        response = {**response, 'result': [], 'embedding_count': len(vectors)}
    yield json.dumps(response, ensure_ascii=False, allow_nan=False) + '\n'
