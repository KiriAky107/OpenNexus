"""Pure raw-graph comparators for a future owned UI case, not runtime evidence."""
import json
import math


def require(value, message):
    if not value:
        raise RuntimeError(message)


def graph(raw):
    require(type(raw) is bytes and 0 < len(raw) <= 262144, 'GRAPH_BYTES_BOUND')
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'GRAPH_DUPLICATE_JSON_KEY')
            result[key] = value
        return result
    value = json.loads(raw.decode('utf-8', 'strict'), object_pairs_hook=pairs, parse_constant=lambda _: (_ for _ in ()).throw(ValueError('GRAPH_NONFINITE')))
    require(type(value) is dict and type(value.get('nodes')) is list and type(value.get('edges')) is list
            and len(value['nodes']) <= 32 and len(value['edges']) <= 64, 'GRAPH_FIXTURE_BOUND')
    identities = set()
    for node in value['nodes']:
        require(type(node) is dict and type(node.get('id')) is str and 0 < len(node['id']) <= 128
                and node['id'] not in identities and node.get('type') in ('text', 'file', 'link', 'group'), 'GRAPH_NODE_ID')
        identities.add(node['id'])
        require(all(type(node.get(k)) in (int, float) and math.isfinite(node[k]) for k in ('x', 'y', 'width', 'height'))
                and node['width'] > 0 and node['height'] > 0, 'GRAPH_GEOMETRY')
    edges = set()
    for edge in value['edges']:
        require(type(edge) is dict and type(edge.get('id')) is str and 0 < len(edge['id']) <= 128
                and edge['id'] not in edges and edge.get('fromNode') in identities and edge.get('toNode') in identities,
                'GRAPH_EDGE_ID_OR_ENDPOINT')
        edges.add(edge['id'])
    return value


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, separators=(',', ':')) + '\n').encode()


def roots_same(before, after):
    require({k:v for k,v in before.items() if k not in ('nodes','edges')}
            == {k:v for k,v in after.items() if k not in ('nodes','edges')}, 'GRAPH_ROOT_EXTENSION_CHANGED')


def added_node(before_raw, after_raw, kind, content):
    before, after = graph(before_raw), graph(after_raw)
    roots_same(before, after)
    existing = after['nodes'][1:] if kind == 'group' else after['nodes'][:-1]
    require(after['edges'] == before['edges'] and existing == before['nodes']
            and len(after['nodes']) == len(before['nodes']) + 1, 'GRAPH_NOT_ONE_NODE_INSERT')
    node = after['nodes'][0] if kind == 'group' else after['nodes'][-1]
    key = {'text':'text','file':'file','link':'url','group':'label'}[kind]
    require(node['type'] == kind and node.get(key) == content, 'GRAPH_NEW_NODE_CONTENT')
    return node['id']


def edited_node(before_raw, after_raw, original_id, fields):
    before, after = graph(before_raw), graph(after_raw)
    roots_same(before, after)
    require(type(fields) is dict and fields and not {'id','type'}.intersection(fields), 'GRAPH_EDIT_FIELDS')
    expected = json.loads(json.dumps(before))
    targets = [n for n in expected['nodes'] if n['id'] == original_id]
    require(len(targets) == 1, 'GRAPH_ORIGINAL_NODE_MISSING')
    targets[0].update(fields)
    require(after == expected, 'GRAPH_EDIT_CHANGED_OTHER_CONTENT')
    return original_id


def added_edge(before_raw, after_raw, source_id, destination_id):
    before, after = graph(before_raw), graph(after_raw)
    roots_same(before, after)
    require(after['nodes'] == before['nodes'] and after['edges'][:-1] == before['edges']
            and len(after['edges']) == len(before['edges']) + 1, 'GRAPH_NOT_ONE_EDGE_APPEND')
    edge = after['edges'][-1]
    require(edge['fromNode'] == source_id and edge['toNode'] == destination_id, 'GRAPH_CONNECTION_DIRECTION_CHANGED')
    return edge['id']


def edited_edge(before_raw, after_raw, original_id, fields):
    before, after = graph(before_raw), graph(after_raw)
    roots_same(before, after)
    require(type(fields) is dict and fields and 'id' not in fields, 'GRAPH_EDGE_FIELDS')
    expected = json.loads(json.dumps(before))
    targets = [e for e in expected['edges'] if e['id'] == original_id]
    require(len(targets) == 1, 'GRAPH_ORIGINAL_EDGE_MISSING')
    targets[0].update(fields)
    require(after == expected, 'GRAPH_EDGE_EDIT_CHANGED_OTHER_CONTENT')
    return original_id


def pasted_selection(before_raw, after_raw, selected):
    before, after = graph(before_raw), graph(after_raw)
    roots_same(before, after)
    require(type(selected) is list and len(selected) == len(set(selected)) and 1 <= len(selected) <= 4, 'GRAPH_SELECTION')
    original = [n for n in before['nodes'] if n['id'] in selected]
    require(len(original) == len(selected), 'GRAPH_SELECTION_ORIGINAL_MISSING')
    new = after['nodes'][len(before['nodes']):]
    require(after['nodes'][:len(before['nodes'])] == before['nodes'] and len(new) == len(original), 'GRAPH_PASTE_NODE_PREFIX')
    mapping = {}
    for first, second in zip(original, new):
        expected = dict(first, id=second['id'], x=first['x']+40, y=first['y']+40)
        require(second == expected, 'GRAPH_PASTE_CONTENT_OR_OFFSET_CHANGED')
        mapping[first['id']] = second['id']
    retained_edges = [e for e in before['edges'] if e['fromNode'] in mapping and e['toNode'] in mapping]
    copies = after['edges'][len(before['edges']):]
    require(after['edges'][:len(before['edges'])] == before['edges'] and len(copies) == len(retained_edges), 'GRAPH_PASTE_EDGE_PREFIX')
    for first, second in zip(retained_edges, copies):
        expected = dict(first, id=second['id'], fromNode=mapping[first['fromNode']], toNode=mapping[first['toNode']])
        require(second == expected, 'GRAPH_PASTE_EDGE_CHANGED')
    return mapping


def removed_selection(before_raw, after_raw, selected, edge_id=None):
    before, after = graph(before_raw), graph(after_raw)
    roots_same(before, after)
    require(type(selected) is list and len(selected) == len(set(selected)) and (selected or edge_id), 'GRAPH_REMOVE_SELECTION')
    require(all(any(n['id'] == identity for n in before['nodes']) for identity in selected), 'GRAPH_REMOVE_ORIGINAL_MISSING')
    require(edge_id is None or any(e['id'] == edge_id for e in before['edges']), 'GRAPH_REMOVE_EDGE_MISSING')
    expected = dict(before,
        nodes=[n for n in before['nodes'] if n['id'] not in selected],
        edges=[e for e in before['edges'] if e['id'] != edge_id and e['fromNode'] not in selected and e['toNode'] not in selected])
    require(after == expected, 'GRAPH_REMOVAL_CHANGED_OTHER_CONTENT')


def identical_original_bytes(expected, observed):
    graph(expected)
    graph(observed)
    require(expected == observed, 'GRAPH_ORIGINAL_BYTES_CHANGED')
