"""Owned local reference fixtures; setup is not actual UI/navigation evidence."""
import json
from copy import deepcopy

SOURCE_MD='reference-source.md'
SOURCE_CANVAS='reference-source.canvas'
TARGET_MD='reference-target-中文.md'
TARGET_CANVAS='reference-target-中文.canvas'
MISSING='reference-missing.md'
UNSAFE='javascript:owned_reference_must_stay_inert'
NODE_MD='c2b8fd22-5a1d-45cd-9806-9da0f4a0e001'
NODE_CANVAS='c2b8fd22-5a1d-45cd-9806-9da0f4a0e002'
TARGET_NODE='c2b8fd22-5a1d-45cd-9806-9da0f4a0e003'
TARGET_BODY='# Owned referenced note 😀\n\nOriginal destination 中文 / unchanged.\n'
TARGET_CANVAS_TEXT='Owned referenced Canvas 中文 😀'
SOURCE_BODY=('# Owned local references 😀\n\n'
    f'[Owned note target]({TARGET_MD})\n\n'
    f'[Owned canvas target]({TARGET_CANVAS})\n\n'
    f'[Missing owned target]({MISSING})\n\n'
    f'[Unsupported original scheme]({UNSAFE})\n')
DRAFT_BODY=SOURCE_BODY+'\nUnconfirmed original reference draft 中文 😀\n'
PATHS=(SOURCE_MD,SOURCE_CANVAS,TARGET_MD,TARGET_CANVAS)
STEPS=('reference-md-target-seed','reference-canvas-target-seed','reference-md-source-seed',
    'reference-canvas-source-seed','reference-baseline','reference-md-draft','reference-md-cancel',
    'reference-md-note','reference-md-return','reference-md-canvas','reference-canvas-note',
    'reference-canvas-canvas','reference-missing','reference-unsupported','reference-cold')

def canvas(value):return (json.dumps(value,ensure_ascii=False,indent=2)+'\n').replace('\n','\r\n').encode()

def originals():
    target=canvas({'nodes':[{'id':TARGET_NODE,'type':'text','x':0,'y':0,'width':260,'height':120,
        'text':TARGET_CANVAS_TEXT,'vendor':{'literal':['<script>not HTML</script>',True,None]}}],
        'edges':[],'vendor-root':{'original':'目标😀'}})
    source=canvas({'nodes':[{'id':NODE_MD,'type':'file','x':0,'y':0,'width':240,'height':120,
        'file':TARGET_MD,'vendor':{'keep':['笔记',17]}},
        {'id':NODE_CANVAS,'type':'file','x':280,'y':0,'width':240,'height':120,
        'file':TARGET_CANVAS,'vendor':{'keep':['画布',19]}}],
        'edges':[],'vendor-root':{'original':['原引用',False,None]}})
    return {SOURCE_MD:SOURCE_BODY.encode(),SOURCE_CANVAS:source,TARGET_MD:TARGET_BODY.encode(),TARGET_CANVAS:target}

def same_originals(expected,observed):
    if set(expected)!=set(PATHS) or set(observed)!=set(PATHS):raise RuntimeError('REFERENCE_EXACT_PATH_SET')
    if any(type(expected[p]) is not bytes or observed[p]!=expected[p] for p in PATHS):raise RuntimeError('REFERENCE_ORIGINAL_BYTES_CHANGED')

def stable_state(value):
    if set(value)!=set(PATHS):raise RuntimeError('REFERENCE_EXACT_STATE_PATH_SET')
    result=deepcopy(value)
    for item in result.values():
        if set(item)!= {'files','drafts','saves','outbox','operations'}:raise RuntimeError('REFERENCE_STATE_SCHEMA')
        for draft in item['drafts']:draft.pop('edit_session',None)
    return result
