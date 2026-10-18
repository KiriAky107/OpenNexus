"""Explicit reserved-domain external URI fixture and receipt admission, not OS proof."""
import json,re

HOST='owned-android-reference.invalid'
MD='https://'+HOST+'/original-md?literal=%25_%26&utf8=%E4%B8%AD%E6%96%87#original'
CANVAS='HTTPS://'+HOST+'/original-canvas?literal=%252F&utf8=%F0%9F%98%80#original'
CANVAS_DELIVERED='https'+CANVAS[5:]
DRIVER='cc.kronecker.opennexus.testdriver'
MD_PATH='external-source.md';CANVAS_PATH='external-source.canvas'
BODY='# Owned original external address 😀\n\n[Owned external Markdown]('+MD+')\n'
DRAFT=BODY+'\nUnconfirmed original external draft 中文 😀\n'
STEPS=tuple('external-'+s for s in ('md-seed','canvas-seed','baseline','draft','cancel','md-open','canvas-open','cold'))
UUID=re.compile(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}')

def require(value,code):
    if not value:raise RuntimeError(code)

def addresses(key):
    require(key in ('markdown','canvas'),'EXTERNAL_OWNED_KEY')
    return (MD,MD) if key=='markdown' else (CANVAS,CANVAS_DELIVERED)

def armed(key,operation):
    original,delivered=addresses(key);require(type(operation) is str and UUID.fullmatch(operation),'EXTERNAL_ORIGINAL_OPERATION_ID')
    return dict(schema=1,scope='external-links',key=key,operation_id=operation,original_uri=original,expected_delivered_uri=delivered)

def decode(raw):
    require(type(raw) is bytes and 0<len(raw)<=8192,'EXTERNAL_RECEIPT_BOUND')
    def pairs(values):
        result={}
        for name,value in values:
            require(name not in result,'EXTERNAL_DUPLICATE_JSON_KEY');result[name]=value
        return result
    return json.loads(raw.decode('utf-8','strict'),object_pairs_hook=pairs)

def validate_record(original,raw):
    require(type(original) is dict and set(original)=={'schema','scope','key','operation_id','original_uri','expected_delivered_uri'},'EXTERNAL_ARMED_SCHEMA')
    require(type(original['schema']) is int and original==armed(original['key'],original['operation_id']),'EXTERNAL_ARMED_CONTENT')
    value=decode(raw)
    require(type(value) is dict and set(value)=={'schema','scope','key','operation_id','actual_action','actual_browsable',
        'actual_data_uri','receiver_package','elapsed_realtime_ms','original_uri','browser_or_network_or_physical_verified'},'EXTERNAL_RECORD_SCHEMA')
    require(type(value['schema']) is int and value['schema']==1 and value['scope']=='external-links','EXTERNAL_RECORD_SCOPE')
    require(value['key']==original['key'] and value['operation_id']==original['operation_id'] and value['original_uri']==original['original_uri'],
        'EXTERNAL_RECEIPT_NOT_ORIGINAL_INTENT')
    require(value['actual_action']=='android.intent.action.VIEW' and value['actual_browsable'] is True,'EXTERNAL_NOT_VIEW_BROWSABLE')
    require(value['actual_data_uri']==original['expected_delivered_uri'] and value['receiver_package']==DRIVER,'EXTERNAL_WRONG_URI_OR_RECEIVER')
    require(type(value['elapsed_realtime_ms']) is int and 0<=value['elapsed_realtime_ms']<2**63,'EXTERNAL_MONOTONIC_TIME')
    require(value['browser_or_network_or_physical_verified'] is False,'EXTERNAL_UNSUPPORTED_CLAIM')
    return value

def canvas_fixture():
    value=dict(nodes=[dict(id='b63ff023-4d16-4af1-91c4-b5b0d8d6e001',type='link',x=0,y=0,width=300,height=140,
        url=CANVAS,vendor={'original':['中文',True,None]})],edges=[],**{'vendor-root':{'original':'external URI 😀'}})
    return (json.dumps(value,ensure_ascii=False,indent=2)+'\n').replace('\n','\r\n').encode()
