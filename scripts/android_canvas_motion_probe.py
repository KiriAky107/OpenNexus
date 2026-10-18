"""One new owned actual-touch scene; exact raw graph, original Native decisions and input traces."""
from contextlib import closing
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shlex
import sqlite3
import tempfile
from android_auth_fixture import atomic_json
from android_runtime_probe import APP, DRIVER, require, require_hosted_emulator
from android_canvas_graph_contract import graph, encoded
from android_canvas_graph_probe import saved_original

PATH = 'motion-original.canvas'
NODE = 'Original motion 😀'
OTHER = 'https://example.invalid/owned-motion'
EDGE = 'Original motion edge'
A, B, E = ('8792bc6e-cd56-45fa-8f5c-645ba12ae901', '8792bc6e-cd56-45fa-8f5c-645ba12ae902', '8792bc6e-cd56-45fa-8f5c-645ba12ae903')
FIXTURE = encoded({'nodes': [dict(id=A, type='text', x=40, y=40, width=160, height=140,
    text=NODE, vendor={'keep': ['原件', True, None]}), dict(id=B, type='link', x=330, y=40,
    width=100, height=140, url=OTHER, vendor={'keep': ['另一节点', 17]})],
    'edges': [dict(id=E, fromNode=A, toNode=B, label=EDGE, vendor={'keep': ['连线', False]})],
    'vendor-root': {'original': ['声明的合成原件', 19, None]}}).replace(b'\n', b'\r\n')
STEPS = tuple('motion-' + value for value in ('seed', 'open', 'move', 'resize', 'pan', 'pinch',
    'cancel', 'takeover', 'background', 'cold', 'inspector'))
TOUCH = STEPS[2:9]


def digest(raw): return hashlib.sha256(raw).hexdigest()


def changed_node(before, after, changes):
    original = graph(before); expected = copy.deepcopy(original)
    require({node['id'] for node in expected['nodes']} == {A, B}
        and len(expected['nodes']) == 2 and len(expected['edges']) == 1 and expected['edges'][0]['id'] == E,
        'MOTION_DECLARED_ORIGINAL_GRAPH_ONLY')
    require(type(changes) is dict and set(changes) in ({'x', 'y'}, {'width', 'height'})
        and all(type(value) is int for value in changes.values()), 'MOTION_EXPLICIT_GEOMETRY_ONLY')
    next(node for node in expected['nodes'] if node['id'] == A).update(changes)
    require(graph(after) == expected, 'MOTION_CHANGED_OTHER_GEOMETRY_OR_ORIGINAL_CONTENT')


def readonly(before, after, original, current, *, cold=False):
    require(type(cold) is bool and current == original, 'MOTION_READONLY_RAW_CHANGED')
    left, right = copy.deepcopy(before), copy.deepcopy(after)
    if cold:
        require(len(left['drafts']) == len(right['drafts']) == 1, 'MOTION_COLD_DRAFT_COUNT')
        old = left['drafts'][0].pop('edit_session'); new = right['drafts'][0].pop('edit_session')
        require(type(old) is type(new) is str and 1 <= len(old) <= 128 and 1 <= len(new) <= 128
            and old != new, 'MOTION_COLD_NOT_NEW_EDIT_SESSION')
    require(left == right, 'MOTION_READONLY_NATIVE_OR_DRAFT_CHANGED')


def rectangle(value):
    require(type(value) is list and len(value) == 4 and all(type(n) is int and abs(n) <= 131072 for n in value)
        and value[2] > value[0] and value[3] > value[1], 'MOTION_RECT_REFUSED')
    return value


def inside(box, x, y, margin=0):
    return box[0] + margin <= x < box[2] - margin and box[1] + margin <= y < box[3] - margin


def actual_input_result(intent, result, phase):
    view=phase in ('view-pan','view-pinch','view-cancel','view-takeover','view-background')
    require((view or phase in TOUCH) and intent['schema'] == result['schema'] == 1
        and intent['scope'] == ('canvas-view' if view else 'canvas-motion') and intent['phase'] == result['phase'] == phase
        and intent['owner'] == result['owner'] == APP, 'MOTION_ORIGINAL_INPUT_SCOPE')
    clip = rectangle(intent['clip']); node = rectangle(intent['node_bounds'])
    controls = intent['controls']; obstacles = intent['obstacles']; plan = intent['plan']
    require(type(controls) is list and type(obstacles) is list and len(controls) <= 32 and len(obstacles) <= 32,
        'MOTION_OBSTACLE_BOUND')
    for box in controls + obstacles: rectangle(box)
    require(inside(clip, node[0], node[1], 4) and inside(clip, node[2]-1, node[3]-1, 4), 'MOTION_ORIGINAL_NODE_CLIPPED')
    require(type(plan) is list and len(plan) == 4 and all(type(n) in (int, float) and math.isfinite(n) for n in plan),
        'MOTION_INPUT_PLAN_REFUSED')
    for x, y in zip(plan[::2], plan[1::2]): require(inside(clip, x, y, 12), 'MOTION_INPUT_OUTSIDE_OWN_VIEWPORT')
    step = phase.removeprefix('view-' if view else 'motion-')
    def clear(x, y):
        return inside(clip,x,y,16) and not any(inside([b[0]-12,b[1]-12,b[2]+12,b[3]+12],x,y) for b in obstacles)
    if step in ('pan','pinch'):
        require(clear(*plan[:2]) and clear(*plan[2:]), 'MOTION_BACKGROUND_POINT_HITS_NODE_OR_CONTROL')
    if step == 'pan':
        require(abs(plan[2]-plan[0]) == 32 and abs(plan[3]-plan[1]) == 24, 'MOTION_NOT_DECLARED_PAN')
    if step in ('move', 'resize', 'cancel', 'takeover', 'background'):
        require(abs(plan[2]-plan[0]-(node[2]-node[0])/4) < .01
            and abs(plan[3]-plan[1]-(node[3]-node[1])/4) < .01, 'MOTION_NOT_DECLARED_QUARTER_DRAG')
        if step == 'resize':
            control = rectangle(intent['resize_control'])
            require(control in controls and inside(node, control[0], control[1])
                and inside(node, control[2]-1, control[3]-1)
                and plan[:2] == [(control[0]+control[2])/2, (control[1]+control[3])/2], 'MOTION_NOT_ORIGINAL_RESIZE_CONTROL')
        else:
            require(inside(node, plan[0], plan[1], 4) and not any(inside([b[0]-4,b[1]-4,b[2]+4,b[3]+4],plan[0],plan[1]) for b in controls),
                'MOTION_NODE_DOWN_HITS_CHILD_CONTROL')
    events = result['events']
    expected = [0] + [2]*8 + ([3] if step == 'cancel' else [1])
    counts = [1]*len(expected)
    if step == 'background': expected=[0]+[2]*8;counts=[1]*9
    if step == 'pinch': expected=[0,5]+[2]*8+[6,2,1];counts=[1]+[2]*10+[1,1]
    if step == 'takeover': expected=[0]+[2]*8+[5]+[2]*8+[6,2,1];counts=[1]*9+[2]*10+[1,1]
    require(type(events) is list and len(events) == len(expected) and [v['action'] for v in events] == expected,
        'MOTION_NOT_ORIGINAL_EVENT_SEQUENCE')
    previous = -1; down = events[0]['down_ms']
    require(type(down) is int and down > 0, 'MOTION_ORIGINAL_DOWN_TIME')
    for value, count in zip(events, counts):
        require(type(value['action']) is int and type(value['index']) is int
            and all(type(v) is int for v in value['ids'] + value['tools'])
            and value['ids'] == list(range(count)) and value['tools'] == [1]*count and value['source'] == 4098
            and value['index'] == (1 if value['action'] in (5,6) else 0)
            and value['down_ms'] == down and type(value['time_ms']) is int
            and previous <= value['time_ms'] and 0 <= value['time_ms']-down <= 180000,
            'MOTION_POINTER_IDENTITY_OR_TIME')
        previous = value['time_ms']; points = value['points']
        require(type(points) is list and len(points) == count, 'MOTION_POINTER_COUNT')
        for pair in points:
            require(type(pair) is list and len(pair) == 2 and all(type(n) in (int,float) and math.isfinite(n) for n in pair)
                and inside(clip,*pair), 'MOTION_POINTER_OUTSIDE_CLIP')
    require(events[0]['points'][0] == plan[:2], 'MOTION_ORIGINAL_DOWN_CHANGED')
    def points(value, expected):
        require(len(value['points'])==len(expected) and all(abs(a-b)<=.05
            for pair,wanted in zip(value['points'],expected) for a,b in zip(pair,wanted)), 'MOTION_COORDINATE_SEQUENCE_CHANGED')
    if step in ('move','resize','cancel','pan','background','takeover'):
        for index in range(1,9):points(events[index],[[plan[0]+(plan[2]-plan[0])*index/8,plan[1]+(plan[3]-plan[1])*index/8]])
        if step not in ('background','takeover'):points(events[-1],[plan[2:]])
    if step in ('pinch','takeover'):
        if step=='pinch':
            first,second=plan[:2],plan[2:];center=(first[0]+second[0])/2
            require(abs(second[0]-first[0])>=80 and first[1]==second[1], 'MOTION_DECLARED_PINCH_PAIR')
            end_first=[center+(first[0]-center)*1.1,first[1]];end_second=[center+(second[0]-center)*1.1,second[1]];offset=1
        else:
            second_plan=intent['secondary'];require(type(second_plan) is list and len(second_plan)==4
                and all(type(n) in (int,float) and math.isfinite(n) for n in second_plan), 'MOTION_SECOND_FINGER_PLAN')
            first,second=plan[2:],second_plan[:2];end_first=first;end_second=second_plan[2:];offset=9
            require(clear(*second) and math.dist(first,second)>=80
                and all(abs(actual-(base+(point-base)*1.1))<=.05 for actual,base,point in zip(end_second,first,second)), 'MOTION_SECOND_FINGER_TAKEOVER')
        points(events[offset],[first,second])
        for index in range(1,9):points(events[offset+index],[[a+(b-a)*index/8 for a,b in zip(first,end_first)],
            [a+(b-a)*index/8 for a,b in zip(second,end_second)]])
        points(events[-3],[end_first,end_second]);points(events[-2],[[end_first[0]+8,end_first[1]+8]])
        points(events[-1],[[end_first[0]+8,end_first[1]+8]])
    rectangle(result['after_node_bounds'])
    require(result['before_node_bounds'] == node and result['owned_root_after'], 'MOTION_RENDERED_ORIGINAL_CHANGED')
    if step == 'background':
        require(result['background_seen'] and result['resumed_owned']
            and re.fullmatch(re.escape(APP)+r'/[A-Za-z0-9_.]+',intent['resume_activity']), 'MOTION_NOT_ACTUAL_OWN_BACKGROUND')
    else: require(not result['background_seen'] and not result['resumed_owned'], 'MOTION_FALSE_BACKGROUND_CLAIM')
    return {'phase':phase,'events':len(events),'actual_background':step=='background'}


def touch_result(intent, result, phase):
    require(phase in TOUCH,'MOTION_ORIGINAL_INPUT_SCOPE')
    observed=actual_input_result(intent,result,phase)
    step=phase.removeprefix('motion-');node=rectangle(intent['node_bounds'])
    clip=rectangle(intent['clip']);plan=intent['plan'];after=rectangle(result['after_node_bounds'])
    if step in ('cancel','background'):
        require(all(abs(a-b) <= 1 for a,b in zip(node,after)), 'MOTION_CANCEL_LEFT_NODE_PREVIEW')
    if step == 'pan':
        require(all(abs((after[i]-node[i])-(plan[i%2+2]-plan[i%2])) <= 1 for i in range(4)), 'MOTION_ACTUAL_PAN_NOT_OBSERVED')
    if step in ('pinch','takeover'):
        require(inside(clip, after[0], after[1], 1) and inside(clip, after[2]-1, after[3]-1, 1)
            and abs((after[2]-after[0])-1.1*(node[2]-node[0])) <= 3
            and abs((after[3]-after[1])-1.1*(node[3]-node[1])) <= 3, 'MOTION_ACTUAL_PINCH_NOT_OBSERVED')
    return observed


def snapshot(probe, phase):
    path=PATH
    if probe.args.scope in ('canvas-view','canvas-view-remaining','canvas-view-cold'):
        from android_canvas_view_probe import PATH as view_path,STEPS as all_view_steps,REMAINING_STEPS,COLD_STEPS
        view_steps=COLD_STEPS if probe.args.scope=='canvas-view-cold' else REMAINING_STEPS if probe.args.scope=='canvas-view-remaining' else all_view_steps
        require(phase.removesuffix('-failure') in view_steps,'MOTION_SNAPSHOT_SCOPE');path=view_path
    else:require(probe.args.scope == 'canvas-motion' and phase.removesuffix('-failure') in STEPS, 'MOTION_SNAPSHOT_SCOPE')
    value={}
    for key,table,columns in [('drafts','drafts','id,file_id,path,base_hash,generation,saved_generation,content,edit_session'),
            ('saves','save_intents','operation_id,draft_id,generation,file_id,path,expected,content,state,error')]:
        value[key]=[dict(zip(columns.split(','),row)) for row in probe.database_rows(
            'SELECT '+columns+' FROM '+table+' WHERE path=? ORDER BY rowid',(path,))]
    folder=Path(tempfile.mkdtemp(prefix='owned-canvas-motion-',dir=probe.tools)); database=folder/'host.sqlite3'
    for suffix in ('','-wal','-shm'):
        data=probe.private(probe.root+'vault/.ainote/host.sqlite3'+suffix,check=False)
        if data.returncode==0:Path(str(database)+suffix).write_bytes(data.stdout)
    with closing(sqlite3.connect(database.resolve().as_uri()+'?mode=ro',uri=True)) as db:
        require(db.execute('PRAGMA integrity_check').fetchone()==('ok',),'MOTION_NATIVE_INTEGRITY')
        for key,columns in [('files','id,path,hash,revision,deleted'),('journal','operation_id,file_id,path,expected,state')]:
            value[key]=[dict(zip(columns.split(','),row)) for row in db.execute('SELECT '+columns+' FROM '+key+' WHERE path=? ORDER BY rowid',(path,))]
        require(len(value['files'])==1,'MOTION_SINGLE_OWN_FILE')
        columns='operation_id,file_id,revision,path,hash,operation,state'
        value['outbox']=[dict(zip(columns.split(','),row)) for row in db.execute('SELECT '+columns+' FROM outbox WHERE file_id=? ORDER BY operation_id',(value['files'][0]['id'],))]
        ids=sorted({row['operation_id'] for row in value['saves']+value['outbox']});require(1<=len(ids)<=8,'MOTION_OPERATION_BOUND')
        columns='operation_id,fingerprint,state,result'
        value['operations']=[dict(zip(columns.split(','),row)) for row in db.execute('SELECT '+columns+' FROM operations WHERE operation_id IN ('+','.join('?' for _ in ids)+') ORDER BY operation_id',ids)]
    atomic_json(probe.output/(phase+'-native.json'),value,exclusive=True)
    return value


def run_canvas_motion(probe):
    require_hosted_emulator(probe.args.serial);require(probe.args.scope=='canvas-motion','MOTION_EXPLICIT_SCOPE')
    report=dict(passed=False,run_id=os.environ['GITHUB_RUN_ID'],tools_source=probe.args.tools_commit,
        compiled_source=probe.args.commit,APK_sha256=probe.binding['sha256'],synthetic_original_fixture_not_GUI_additions=True,
        physical_properties_upgrade_or_whole_A3C_complete=False,executed_steps=[],saves=[],touches=[])
    current=None
    def records(phase, check=True):
        values=[]
        for kind in ('intent','result'):
            name=phase+'-touch-'+kind+'.json'
            raw=probe.device('exec-out','run-as',DRIVER,'cat','files/'+name,check=False)
            if raw.returncode or not raw.stdout:
                require(not check,'MOTION_ORIGINAL_DRIVER_RECORD_MISSING');return None
            require(len(raw.stdout)<=16384,'MOTION_DRIVER_RECORD_BOUND')
            output=probe.output/name
            if output.exists():require(output.read_bytes()==raw.stdout,'MOTION_ORIGINAL_DRIVER_RECORD_CHANGED')
            else:
                with output.open('xb') as stream:stream.write(raw.stdout);stream.flush();os.fsync(stream.fileno())
            values.append(json.loads(raw.stdout))
        return values
    def phase(name):
        nonlocal current
        require(name==STEPS[len(report['executed_steps'])],'MOTION_ORIGINAL_PHASE_ORDER');current=name
        atomic_json(probe.output/(name+'-original-intent.json'),dict(scope='canvas-motion',phase=name,
            path=PATH,known_original_file_id=report['saves'][0]['file_id'] if report['saves'] else None,
            previous_observed_sha256=report.get('last_observed_sha256'),
            planned_geometry={'x':80,'y':75} if name=='motion-move' else {'width':200,'height':175} if name=='motion-resize' else None,
            tools_source=probe.args.tools_commit,compiled_source=probe.args.commit,automatic_retry=False),exclusive=True)
        old=probe.shell('pidof',APP,check=False).stdout.strip()
        probe.phase(name,continue_process=name not in ('motion-seed','motion-open','motion-cold'),keep_running=True)
        new=probe.shell('pidof',APP).stdout.strip()
        require(re.fullmatch(rb'[1-9][0-9]{0,9}',new),'MOTION_NO_ORIGINAL_APP_PID')
        if name not in ('motion-seed','motion-open','motion-cold'):require(new==old,'MOTION_ORIGINAL_PROCESS_CHANGED')
        elif name!='motion-seed':require(old!=new,'MOTION_NOT_COLD_NEW_PID')
        if name in TOUCH:report['touches'].append(touch_result(*records(name),name))
        report['executed_steps'].append(name)
        native=snapshot(probe,name);raw=probe.original(PATH);report['last_observed_sha256']=digest(raw)
        return native,raw,new
    try:
        probe.enable_test_ime()
        before,raw,_=phase('motion-seed');require(graph(raw)=={'nodes':[],'edges':[]},'MOTION_GUI_EMPTY_SEED')
        report['saves'].append(saved_original(None,before,raw,path=PATH))
        atomic_json(probe.output/'motion-fixture-original-intent.json',dict(path=PATH,before_sha256=digest(raw),
            declared_synthetic_sha256=digest(FIXTURE),same_new_owned_file_only=True,not_GUI_additions=True,automatic_retry=False),exclusive=True)
        target=probe.root+'vault/'+PATH;require(probe.private(target).stdout==raw,'MOTION_SEED_CHANGED_BEFORE_DECLARED_FIXTURE')
        probe.device('shell','-T',shlex.join(('run-as',APP,'sh','-c','cat > '+shlex.quote(target))),input=FIXTURE)
        require(probe.private(target).stdout==FIXTURE,'MOTION_SYNTHETIC_ORIGINAL_BYTES')
        report['last_observed_sha256']=digest(FIXTURE)
        before,raw,opened_pid=phase('motion-open');require(raw==FIXTURE,'MOTION_OPEN_REWROTE_ORIGINAL')
        for name,changes in [('motion-move',{'x':80,'y':75}),('motion-resize',{'width':200,'height':175})]:
            after,new,_=phase(name);changed_node(raw,new,changes);require(not after['journal'],'MOTION_FINISHED_SAVE_LEFT_JOURNAL')
            report['saves'].append(saved_original(before,after,new,path=PATH));before,raw=after,new
        final_pid=None
        for name in STEPS[4:]:
            after,new,pid=phase(name);readonly(before,after,raw,new,cold=name=='motion-cold')
            before=after
            if name=='motion-cold':final_pid=pid
        report.update(passed=True,original_file_id=before['files'][0]['id'],fixture_open_PID=int(opened_pid),cold_PID=int(final_pid),
            final_sha256=digest(raw),actual_two_geometry_Saves=len(report['saves'])==3,view_and_interrupted_gestures_have_no_new_Save=True)
    except Exception as error:
        report.update(failure_class=type(error).__name__,phase=current)
        if current in TOUCH:
            try:records(current,check=False)
            except Exception:report['driver_failure_record_unavailable']=True
        if probe.root is not None and current in STEPS:
            try:snapshot(probe,current+'-failure')
            except Exception as observation:report['failure_observation_class']=type(observation).__name__
        raise
    finally:
        atomic_json(probe.output/'canvas-motion-proof.json',report,exclusive=True);probe.force_stop()
