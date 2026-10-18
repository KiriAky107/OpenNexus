"""One owned fixed-APK GUI graph scene; raw graphs and original native receipts."""
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import sqlite3
import tempfile
from android_auth_fixture import atomic_json
from android_runtime_probe import APP, require, require_hosted_emulator
from android_canvas_graph_contract import (graph, encoded, added_node, added_edge, edited_edge,
    pasted_selection, removed_selection, identical_original_bytes)

PATH='graph-original.canvas'
SEED_A,SEED_B='Original alpha','Original beta'
TEXT,GROUP='实际中文输入 😀','实际分组 😀'
FILE,URL='资料/原笔记😀.md','https://example.invalid/owned-canvas'
EDGE='实际连接 😀'
A,B,E=('f58f7b80-8bf1-42da-a619-f1e08c70c301','f58f7b80-8bf1-42da-a619-f1e08c70c302','f58f7b80-8bf1-42da-a619-f1e08c70c303')
FIXTURE=encoded({'nodes':[dict(id=A,type='text',x=0,y=0,width=120,height=100,text=SEED_A,vendor={'keep':['甲',True,None]}),
    dict(id=B,type='text',x=150,y=0,width=120,height=100,text=SEED_B,vendor={'keep':['乙',17]})],
    'edges':[dict(id=E,fromNode=A,toNode=B,label='Original seed edge',vendor={'opaque':[True,19]})],
    'vendor-root':{'opaque':['原字节',True,None]}})
STEPS=tuple('graph-'+x for x in ('seed','open','add-text','add-file','add-link','add-group','connect','edge-label',
    'copy','duplicate','undo-duplicate','paste','undo','redo','delete','undo-delete','redo-delete','fit','cold'))
READ_ONLY={'graph-open','graph-copy','graph-fit','graph-cold'}
UUID=r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'

def graph_steps(remaining):
    require(type(remaining) is bool,'GRAPH_EXPLICIT_SELECTION')
    return tuple(x for x in STEPS if not remaining or x not in ('graph-add-text','graph-add-file','graph-add-link'))

def initial_fixture(remaining):
    require(type(remaining) is bool,'GRAPH_EXPLICIT_FIXTURE_SELECTION')
    if not remaining:return FIXTURE,{}
    value=graph(FIXTURE);ids={}
    for kind,content,xy,ending in [('text',TEXT,(0,120),'304'),('file',FILE,(150,120),'305'),('link',URL,(0,240),'306')]:
        identity='f58f7b80-8bf1-42da-a619-f1e08c70c'+ending;ids[kind]=identity
        value['nodes'].append(dict(id=identity,type=kind,x=xy[0],y=xy[1],width=120,height=100,
            **{{'text':'text','file':'file','link':'url'}[kind]:content},vendor={'synthetic_fixture_only':True}))
    return encoded(value),ids

def digest(raw):return hashlib.sha256(raw).hexdigest()

def snapshot(probe,label):
    require(probe.args.scope in ('canvas-graph','canvas-graph-remaining') and label.removesuffix('-failure') in graph_steps(probe.args.scope=='canvas-graph-remaining'),'GRAPH_SNAPSHOT_SCOPE')
    value={}
    for key,table,columns in [('drafts','drafts','id,file_id,path,base_hash,generation,saved_generation,content,edit_session'),
        ('saves','save_intents','operation_id,draft_id,generation,file_id,path,expected,content,state,error')]:
        value[key]=[dict(zip(columns.split(','),row)) for row in probe.database_rows(
            'SELECT '+columns+' FROM '+table+' WHERE path=? ORDER BY rowid',(PATH,))]
    folder=Path(tempfile.mkdtemp(prefix='owned-canvas-graph-native-',dir=probe.tools));database=folder/'host.sqlite3'
    for suffix in ('','-wal','-shm'):
        raw=probe.private(probe.root+'vault/.ainote/host.sqlite3'+suffix,check=False)
        if raw.returncode==0:Path(str(database)+suffix).write_bytes(raw.stdout)
    with closing(sqlite3.connect(database.resolve().as_uri()+'?mode=ro',uri=True)) as db:
        require(db.execute('PRAGMA integrity_check').fetchone()==('ok',),'GRAPH_NATIVE_INTEGRITY')
        columns='id,path,hash,revision,deleted'
        value['files']=[dict(zip(columns.split(','),row)) for row in db.execute('SELECT '+columns+' FROM files WHERE path=?',(PATH,))]
        require(len(value['files'])==1,'GRAPH_SINGLE_OWN_FILE')
        columns='operation_id,file_id,revision,path,hash,operation,state'
        value['outbox']=[dict(zip(columns.split(','),row)) for row in db.execute(
            'SELECT '+columns+' FROM outbox WHERE file_id=? ORDER BY operation_id',(value['files'][0]['id'],))]
        ids=sorted({x['operation_id'] for x in value['outbox']+value['saves']})
        require(1<=len(ids)<=64,'GRAPH_OPERATION_BOUND')
        columns='operation_id,fingerprint,state,result'
        value['operations']=[dict(zip(columns.split(','),row)) for row in db.execute(
            'SELECT '+columns+' FROM operations WHERE operation_id IN ('+','.join('?' for _ in ids)+') ORDER BY operation_id',ids)]
    atomic_json(probe.output/(label+'-native.json'),value,exclusive=True)
    return value

def projection(value):return {key:value[key] for key in ('saves','files','outbox','operations')}

def saved_original(before,after,raw):
    """Only one actual new Save, bound to its unchanged original operation and file."""
    require(len(after['files'])==len(after['drafts'])==1,'GRAPH_SAVE_SINGLE_FILE')
    entry,draft=after['files'][0],after['drafts'][0]
    require(re.fullmatch(UUID,entry['id']) and entry['path']==PATH and entry['hash']==digest(raw) and entry['deleted']==0,
        'GRAPH_SAVE_NATIVE_FILE')
    require(draft['file_id']==entry['id'] and draft['content'].encode()==raw and draft['generation']==draft['saved_generation'],
        'GRAPH_SAVE_DRAFT_NOT_ORIGINAL')
    old=before['saves'] if before else []
    require(len(after['saves'])==len(old)+1 and after['saves'][:len(old)]==old,'GRAPH_SAVE_ORIGINAL_INTENT_REPLACED')
    intent=after['saves'][-1];operation=intent['operation_id']
    require(re.fullmatch(UUID,operation) and intent['state']=='committed' and intent['error'] is None
        and intent['path']==PATH and intent['content'].encode()==raw and intent['file_id'] in (None,entry['id']),
        'GRAPH_SAVE_INTENT_CONTENT')
    receipts=[x for x in after['operations'] if x['operation_id']==operation]
    outbox=[x for x in after['outbox'] if x['operation_id']==operation]
    require(len(receipts)==len(outbox)==1 and receipts[0]['state']=='committed'
        and re.fullmatch(r'[0-9a-f]{64}',receipts[0]['fingerprint']),'GRAPH_SAVE_ORIGINAL_RECEIPT')
    result=json.loads(receipts[0]['result']);pending=outbox[0]
    require(result['file_id']==pending['file_id']==entry['id'] and result['path']==pending['path']==PATH
        and result['hash']==pending['hash']==digest(raw) and result['deleted'] is False
        and result['revision']==pending['revision']==entry['revision'] and pending['operation']=='put','GRAPH_SAVE_ORIGINAL_RESULT')
    if before:
        require(before['files'][0]['id']==entry['id'] and entry['revision']==before['files'][0]['revision']+1
            and intent['file_id']==entry['id'] and intent['expected']==before['files'][0]['hash'],'GRAPH_SAVE_CHANGED_ID_OR_CAS')
        for key in ('operations','outbox'):
            require(all(x in after[key] for x in before[key]) and len(after[key])==len(before[key])+1,'GRAPH_SAVE_REPLAYED_OR_REPLACED')
    return {'file_id':entry['id'],'operation_id':operation,'fingerprint':receipts[0]['fingerprint'],'sha256':digest(raw)}

def run_canvas_graph(probe):
    require_hosted_emulator(probe.args.serial);require(probe.args.scope in ('canvas-graph','canvas-graph-remaining'),'GRAPH_EXPLICIT_SCOPE')
    remaining=probe.args.scope=='canvas-graph-remaining';planned=graph_steps(remaining);fixture,fixture_ids=initial_fixture(remaining)
    report={'passed':False,'run_id':os.environ['GITHUB_RUN_ID'],'tools_source':probe.args.tools_commit,
        'compiled_source':probe.args.commit,'APK_sha256':probe.binding['sha256'],'scope':probe.args.scope,
        'three_type_GUI_additions_and_composition_not_replayed':remaining,'synthetic_fixture_three_type_ids':fixture_ids,
        'physical_phone_touch_geometry_midwrite_or_whole_A3C_complete':False,'executed_steps':[],'saves':[]}
    current=None
    def phase(name):
        nonlocal current
        require(name==planned[len(report['executed_steps'])],'GRAPH_ORIGINAL_STEP_ORDER');current=name;report['phase']=name
        atomic_json(probe.output/(name+'-original-intent.json'),{'run_id':os.environ['GITHUB_RUN_ID'],
            'tools_source':probe.args.tools_commit,'compiled_source':probe.args.commit,'scope':probe.args.scope,'step':name,
            'owned_path':PATH,'automatic_retry':False,'native_operation_ID_assigned_by_driver':False},exclusive=True)
        probe.phase(name,continue_process=name not in ('graph-seed','graph-open','graph-cold'),keep_running=True)
        value=snapshot(probe,name);raw=probe.original(PATH);graph(raw)
        report['executed_steps'].append(name)
        return value,raw
    try:
        probe.enable_test_ime()
        before,raw=phase('graph-seed');require(graph(raw)=={'nodes':[],'edges':[]},'GRAPH_GUI_EMPTY_CREATION')
        report['saves'].append(saved_original(None,before,raw));original_id=before['files'][0]['id']
        original_pid=probe.shell('pidof',APP).stdout.strip();require(re.fullmatch(rb'[1-9][0-9]{0,9}',original_pid),'GRAPH_ORIGINAL_PID')
        atomic_json(probe.output/'graph-extension-fixture-original-intent.json',{'before_sha256':digest(raw),
            'after_sha256':digest(fixture),'owned_path':PATH,'synthetic_seed_nodes_and_edge_declared':True,
            'actual_GUI_creations_or_unknown_user_file_not_claimed':True,'automatic_retry':False},exclusive=True)
        probe.device('shell','-T',shlex.join(('run-as',APP,'sh','-c','cat > '+shlex.quote(probe.root+'vault/'+PATH))),input=fixture)
        require(probe.original(PATH)==fixture,'GRAPH_OWN_FIXTURE_BYTES')
        before,raw=phase('graph-open');identical_original_bytes(fixture,raw)
        open_pid=probe.shell('pidof',APP).stdout.strip()
        require(re.fullmatch(rb'[1-9][0-9]{0,9}',open_pid) and open_pid!=original_pid,'GRAPH_FIXTURE_COLD_NEW_PID')
        require(before['files'][0]['id']==original_id and before['saves'][0]['operation_id']==report['saves'][0]['operation_id'],'GRAPH_COLD_FIXTURE_LOST_CREATION')
        ids=dict(fixture_ids);actual_added={}
        for kind,content,position in [('text',TEXT,(0,120)),('file',FILE,(150,120)),('link',URL,(0,240)),('group',GROUP,(350,0))]:
            if remaining and kind!='group':continue
            after,new=phase('graph-add-'+kind);identity=added_node(raw,new,kind,content)
            node=next(x for x in graph(new)['nodes'] if x['id']==identity)
            require(tuple(node[k] for k in ('x','y','width','height'))==(*position,120,100),'GRAPH_ACTUAL_PROPERTIES')
            ids[kind]=identity;actual_added[kind]=identity;report['saves'].append(saved_original(before,after,new));before,raw=after,new
        if not remaining:
            require((probe.output/'graph-add-text.log').read_text('utf-8').count(
                'ORIGINAL_GRAPH_IME compose=2 confirm=1 save_during_composition=0')==1,'GRAPH_ACTUAL_COMPOSITION_MARKER')
        after,new=phase('graph-connect');edge=added_edge(raw,new,ids['link'],ids['file'])
        report['saves'].append(saved_original(before,after,new));before,raw=after,new
        after,new=phase('graph-edge-label');edited_edge(raw,new,edge,{'label':EDGE})
        report['saves'].append(saved_original(before,after,new));before,raw=after,new
        copy_state,copy_raw=phase('graph-copy');identical_original_bytes(raw,copy_raw)
        require(projection(copy_state)==projection(before),'GRAPH_COPY_WROTE_NATIVE')
        after,duplicate=phase('graph-duplicate');duplicate_map=pasted_selection(raw,duplicate,[A,B])
        report['saves'].append(saved_original(before,after,duplicate));before=after
        after,new=phase('graph-undo-duplicate');identical_original_bytes(raw,new)
        report['saves'].append(saved_original(before,after,new));before=after
        after,pasted=phase('graph-paste');paste_map=pasted_selection(raw,pasted,[A,B])
        require(set(paste_map.values()).isdisjoint(duplicate_map.values()),'GRAPH_PASTE_REUSED_REMOVED_IDS')
        report['saves'].append(saved_original(before,after,pasted));before=after
        after,new=phase('graph-undo');identical_original_bytes(raw,new)
        report['saves'].append(saved_original(before,after,new));before=after
        after,new=phase('graph-redo');identical_original_bytes(pasted,new)
        report['saves'].append(saved_original(before,after,new));before=after
        after,deleted=phase('graph-delete');removed_selection(pasted,deleted,[ids['link']])
        report['saves'].append(saved_original(before,after,deleted));before=after
        after,new=phase('graph-undo-delete');identical_original_bytes(pasted,new)
        report['saves'].append(saved_original(before,after,new));before=after
        after,new=phase('graph-redo-delete');identical_original_bytes(deleted,new)
        report['saves'].append(saved_original(before,after,new));before=after
        for step in ('graph-fit','graph-cold'):
            if step=='graph-cold':before_cold_pid=probe.shell('pidof',APP).stdout.strip()
            after,new=phase(step);identical_original_bytes(deleted,new)
            require(projection(after)==projection(before),'GRAPH_VIEW_OR_COLD_REPLAYED_SAVE')
        cold_pid=probe.shell('pidof',APP).stdout.strip()
        require(re.fullmatch(rb'[1-9][0-9]{0,9}',before_cold_pid) and re.fullmatch(rb'[1-9][0-9]{0,9}',cold_pid)
            and cold_pid!=before_cold_pid,'GRAPH_ACTUAL_NEW_PID')
        require(tuple(report['executed_steps'])==planned,'GRAPH_INCOMPLETE_STEPS')
        report.update(passed=True,original_file_id=original_id,actual_added_node_ids=actual_added,all_selected_node_ids=ids,actual_edge_id=edge,
            duplicate_mapping=duplicate_map,paste_mapping=paste_map,seed_extension_fixture_is_synthetic=True,
            original_PID=int(original_pid),fixture_open_PID=int(open_pid),before_cold_PID=int(before_cold_pid),cold_PID=int(cold_pid),final_sha256=digest(deleted))
    except Exception as error:
        report['failure_class']=type(error).__name__
        if probe.root is not None and current in STEPS:
            try:snapshot(probe,current+'-failure')
            except Exception as failure:report['failure_observation_class']=type(failure).__name__
        raise
    finally:
        atomic_json(probe.output/'canvas-graph-proof.json',report,exclusive=True);probe.force_stop()
