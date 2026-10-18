"""Declared remaining property transactions; pure data, not UI acceptance."""
from copy import deepcopy
import json
from urllib.parse import urlsplit
from android_canvas_graph_contract import graph,encoded,edited_node,edited_edge,require

PATH='properties-original.canvas'
A='917cc458-6ab0-43bf-8c12-2bc54c0ae101'
B='917cc458-6ab0-43bf-8c12-2bc54c0ae102'
C='917cc458-6ab0-43bf-8c12-2bc54c0ae103'
G='917cc458-6ab0-43bf-8c12-2bc54c0ae104'
E='917cc458-6ab0-43bf-8c12-2bc54c0ae105'
FIXTURE=encoded(dict(nodes=[
    dict(id=G,type='group',x=500,y=0,width=240,height=160,label='Properties group before',background='assets/before.png',vendor={'opaque':['组',False]}),
    dict(id=A,type='text',x=0,y=0,width=160,height=120,text='Properties node before',label='Original node label',color='1',vendor={'opaque':['正文',True,None]}),
    dict(id=B,type='file',x=220,y=0,width=180,height=120,file='reference-before.md',subpath='#before',vendor={'keep':17}),
    dict(id=C,type='link',x=0,y=180,width=160,height=120,url='https://example.invalid/before',vendor={'keep':'<script>literal</script>'})],
    edges=[dict(id=E,fromNode=A,toNode=B,label='Properties edge original',fromSide='right',toSide='left',fromEnd='none',toEnd='arrow',color='2',vendor={'opaque':['连接',19]})],
    **{'vendor-root':{'original':['原始扩展',True,None]}})).replace(b'\n',b'\r\n')
TRANSACTIONS=(
    dict(phase='properties-text',kind='node',identity=A,fields=dict(label='Node label updated 😀',color='4')),
    dict(phase='properties-group',kind='node',identity=G,fields=dict(label='Group label updated 中文',background='assets/background-updated.png')),
    dict(phase='properties-link',kind='node',identity=C,fields=dict(url='https://example.invalid/updated?q=%25_%E4%B8%AD')),
    dict(phase='properties-file',kind='node',identity=B,fields=dict(file='references/updated-中文.md',subpath='#Heading')),
    dict(phase='properties-edge',kind='edge',identity=E,fields=dict(fromNode=G,toNode=C,fromSide='bottom',toSide='top',fromEnd='arrow',toEnd='none',color='6')),
)
STEPS=('properties-seed','properties-open',*(v['phase'] for v in TRANSACTIONS),'properties-cold')


def before_transaction(phase):
    value=graph(FIXTURE)
    for item in TRANSACTIONS:
        if item['phase']==phase:return value,item
        target=next(v for v in value['nodes' if item['kind']=='node' else 'edges'] if v['id']==item['identity'])
        target.update(item['fields'])
    raise RuntimeError('PROPERTY_ORIGINAL_PHASE_REFUSED')

def selected_transactions(remaining):
    require(type(remaining) is bool,'PROPERTY_EXPLICIT_REMAINING_MODE')
    return TRANSACTIONS[-1:] if remaining else TRANSACTIONS

def property_steps(remaining):
    return ('properties-seed','properties-open',*(v['phase'] for v in selected_transactions(remaining)),'properties-cold')

def initial_property_fixture(remaining):
    require(type(remaining) is bool,'PROPERTY_EXPLICIT_REMAINING_MODE')
    return (json.dumps(before_transaction('properties-edge')[0],ensure_ascii=False,indent=2)+'\n').replace('\n','\r\n').encode() if remaining else FIXTURE


def validate_transaction(before_raw,after_raw,phase):
    expected,item=before_transaction(phase)
    require(graph(before_raw)==expected,'PROPERTY_ORIGINAL_BEFORE_CHANGED')
    compare=edited_node if item['kind']=='node' else edited_edge
    compare(before_raw,after_raw,item['identity'],item['fields'])
    require(b'\r\n' in after_raw and b'\n' not in after_raw.replace(b'\r\n',b''),'PROPERTY_CRLF_CHANGED')
    return deepcopy(item)


def declared_fields():return sum(len(v['fields']) for v in TRANSACTIONS)


def driver_form(phase):
    before,item=before_transaction(phase);index={v['id']:v for v in before['nodes']}
    node=index[A] if item['kind']=='edge' else index[item['identity']]
    def title(value):return value.get({'text':'text','group':'label','file':'file','link':'url'}[value['type']],'')
    def caption(key,value):
        if key in ('fromNode','toNode'):return title(index[value])[:80] or value
        if key in ('fromSide','toSide'):return {'left':'Left','right':'Right','top':'Top','bottom':'Bottom'}[value]
        if key in ('fromEnd','toEnd'):return {'none':'No arrow','arrow':'Arrow'}[value]
        return value
    fields=[];target=node if item['kind']=='node' else before['edges'][0]
    names={'label':'Node content' if node['type']=='group' else 'Label','background':'Background image path',
        'url':'Node content','file':'Node content','subpath':'Heading or block subpath',
        'color':'Color' if item['kind']=='edge' else 'Node color','fromNode':'Source node','toNode':'Destination node',
        'fromSide':'From side','toSide':'To side','fromEnd':'From arrow','toEnd':'To arrow'}
    for key,value in item['fields'].items():
        fields.append(dict(key=key,name=names[key],kind='select' if key in ('color','fromNode','toNode','fromSide','toSide','fromEnd','toEnd') else 'input',
            before=caption(key,target[key]),after=caption(key,value)))
    label={'text':title(node).splitlines()[0],'group':title(node),'file':title(node).split('/')[-1],
        'link':urlsplit(title(node)).hostname if node['type']=='link' else ''}[node['type']]
    return dict(schema=1,scope='canvas-properties',phase=phase,target_id=item['identity'],
        target_node_label=node['type']+': '+label,edge_label='Properties edge original' if item['kind']=='edge' else '',fields=fields)
