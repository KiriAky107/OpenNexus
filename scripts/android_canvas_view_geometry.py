"""Pure geometry contracts for a declared remaining-view fixture, never input.

These checks need original native events and exact raw/native preservation in
the eventual runtime coordinator. Geometry alone is not gesture acceptance.
"""
import math,uuid

A='8792bc6e-cd56-45fa-8f5c-645ba12ae901';B='8792bc6e-cd56-45fa-8f5c-645ba12ae902';E='8792bc6e-cd56-45fa-8f5c-645ba12ae903'
MODEL={A:(80,75,200,175),B:(330,40,100,140)}
PHASES=('view-pan','view-pinch','view-cancel','view-takeover','view-background')

def require(value,code):
    if not value:raise ValueError(code)

def number(value,low=-131072,high=131072):
    require(type(value) in (int,float) and math.isfinite(value) and low<=value<=high,'VIEW_NUMBER_REFUSED')
    return value

def rect(value):
    require(type(value) is dict and set(value)=={'left','top','right','bottom','width','height'},'VIEW_RECT_REFUSED')
    for item in value.values():number(item)
    require(value['width']>0 and value['height']>0
        and abs(value['right']-value['left']-value['width'])<=.001
        and abs(value['bottom']-value['top']-value['height'])<=.001,'VIEW_RECT_INCONSISTENT')
    return value

def geometry(value,dispatch,stage):
    require(type(dispatch) is str and str(uuid.UUID(dispatch))==dispatch and stage in ('before','after'),'VIEW_SCOPE_REFUSED')
    require(type(value) is dict and set(value)=={'schema','scope','dispatch_id','stage','observed_ms','time_origin',
        'window','visual','viewport','world','nodes','edges','save'},'VIEW_METADATA_KEYS_REFUSED')
    require(type(value['schema']) is int and value['schema']==1 and value['scope']=='canvas-view-geometry'
        and value['dispatch_id']==dispatch and value['stage']==stage,'VIEW_METADATA_SCOPE_REFUSED')
    number(value['observed_ms'],0,86400000);number(value['time_origin'],1,10**16)
    window=value['window'];visual=value['visual']
    require(type(window) is dict and set(window)=={'width','height','dpr','scroll_x','scroll_y'}
        and type(visual) is dict and set(visual)=={'left','top','width','height','scale'},'VIEW_WINDOW_KEYS_REFUSED')
    number(window['width'],160,8192);number(window['height'],160,16384);number(window['dpr'],.5,8)
    for key in ('scroll_x','scroll_y'):require(abs(number(window[key]))<=.02,'VIEW_PAGE_SCROLL_UNEXPECTED')
    for key in ('left','top'):require(abs(number(visual[key]))<=.02,'VIEW_VISUAL_OFFSET_UNEXPECTED')
    require(abs(number(visual['scale'])-1)<=.001 and all(abs(number(visual[key])-window[key])<=2 for key in ('width','height')),
        'VIEW_BROWSER_ZOOM_OR_VIEWPORT_CHANGED')
    viewport=rect(value['viewport']);world=value['world']
    require(type(world) is dict and set(world)=={'matrix'} and type(world['matrix']) is list and len(world['matrix'])==6,'VIEW_MATRIX_REFUSED')
    matrix=world['matrix']
    for item in matrix:number(item)
    require(.1<=matrix[0]<=3 and abs(matrix[1])+abs(matrix[2])<=.0001 and abs(matrix[3]-matrix[0])<=.0001,'VIEW_NONUNIFORM_TRANSFORM')
    require(type(value['nodes']) is list and len(value['nodes'])==2 and {item.get('id') for item in value['nodes']}==set(MODEL),'VIEW_ORIGINAL_NODE_SET')
    for node in value['nodes']:
        require(set(node)=={'id','rect','selected','resize'} and type(node['selected']) is bool
            and type(node['resize']) is list and len(node['resize'])<=1,'VIEW_NODE_KEYS_REFUSED')
        actual=rect(node['rect']);x,y,width,height=MODEL[node['id']]
        expected=dict(left=viewport['left']+matrix[4]+x*matrix[0],top=viewport['top']+matrix[5]+y*matrix[0],width=width*matrix[0],height=height*matrix[0])
        require(all(abs(actual[key]-wanted)<=.1 for key,wanted in expected.items()),'VIEW_NODE_DOES_NOT_MATCH_ORIGINAL_MODEL')
        for control in node['resize']:
            control=rect(control)
            require(control['left']>=actual['left']-1 and control['top']>=actual['top']-1
                and control['right']<=actual['right']+1 and control['bottom']<=actual['bottom']+1,'VIEW_FOREIGN_RESIZE_CONTROL')
    require(type(value['edges']) is list and len(value['edges'])==1 and set(value['edges'][0])=={'id','rect'}
        and value['edges'][0]['id']==E,'VIEW_ORIGINAL_EDGE_SET')
    rect(value['edges'][0]['rect'])
    require(value['save']=={'enabled':False} and type(value['save']['enabled']) is bool,'VIEW_DIRTY_OR_UNKNOWN_SAVE')
    return value

def native_rect(value):
    require(type(value) is list and len(value)==4 and all(type(item) is int and abs(item)<=131072 for item in value)
        and value[2]>value[0] and value[3]>value[1],'VIEW_NATIVE_RECT_REFUSED')
    return value

def calibrate(value,native,phase):
    require(type(native) is dict and set(native)=={'schema','scope','dispatch_id','phase','stage','owner','webview','clip','node','recorded_ms'},
        'VIEW_NATIVE_METADATA_KEYS')
    require(type(native['schema']) is int and native['schema']==1 and native['scope']=='canvas-view'
        and native['dispatch_id']==value['dispatch_id'] and native['phase']==phase and phase in PHASES
        and native['stage']==value['stage'] and native['owner']=='cc.kronecker.opennexus.dev','VIEW_NATIVE_SCOPE')
    number(native['recorded_ms'],1,10**12)
    webview=native_rect(native['webview']);clip=native_rect(native['clip']);node=native_rect(native['node'])
    require(webview[0]<=clip[0]<=node[0]<node[2]<=clip[2]<=webview[2]
        and webview[1]<=clip[1]<=node[1]<node[3]<=clip[3]<=webview[3],'VIEW_NATIVE_OWNERSHIP_CLIP')
    window=value['window'];ratio=window['dpr'];rounding=max(3,ratio)
    require(abs(webview[2]-webview[0]-window['width']*ratio)<=rounding
        and abs(webview[3]-webview[1]-window['height']*ratio)<=rounding,'VIEW_NATIVE_PIXEL_CALIBRATION')
    viewport=value['viewport']
    mapped=[webview[0]+viewport['left']*ratio,webview[1]+viewport['top']*ratio,
        webview[0]+viewport['right']*ratio,webview[1]+viewport['bottom']*ratio]
    require(all(abs(a-b)<=rounding for a,b in zip(mapped,clip)),'VIEW_NATIVE_VIEWPORT_CALIBRATION')
    return ratio

def frame_check(value,native):
    require(type(value) is dict and set(value)=={'schema','scope','dispatch_id','phase','stage','owner','native_sha256',
        'webview','before_read','after_read','input_target_exact_guard'},'VIEW_FRAME_KEYS')
    require(type(value['schema']) is int and value['schema']==1 and value['scope']=='canvas-view-frame-check'
        and all(value[key]==native[key] for key in ('dispatch_id','phase','stage','owner','webview')),'VIEW_FRAME_SCOPE')
    require(native['scope']=='canvas-view' and native['phase'] in PHASES and native['stage'] in ('before','after')
        and native['owner']=='cc.kronecker.opennexus.dev' and str(uuid.UUID(native['dispatch_id']))==native['dispatch_id'],
        'VIEW_FRAME_NATIVE_ORIGIN')
    window=native_rect(value['webview'])
    require(type(value['native_sha256']) is str and len(value['native_sha256'])==64
        and all(letter in '0123456789abcdef' for letter in value['native_sha256']),'VIEW_FRAME_HASH')
    before,after=value['before_read'],value['after_read']
    require(all(type(rows) is list and 2<=len(rows)<=34 for rows in (before,after)),'VIEW_FRAME_ROWS')
    for rows in (before,after):
        for row in rows:native_rect(row)
        node,clip=rows[:2]
        require(clip[0]<=node[0]<node[2]<=clip[2] and clip[1]<=node[1]<node[3]<=clip[3],'VIEW_FRAME_NODE_OUTSIDE')
    require(before[0]==native['node'] and before[1]==native['clip']==after[1],'VIEW_FRAME_VIEWPORT_CHANGED')
    require(window[0]<=before[1][0]<before[1][2]<=window[2] and window[1]<=before[1][1]<before[1][3]<=window[3],
        'VIEW_FRAME_VIEWPORT_OUTSIDE')
    exact=native['stage']=='before'
    require(type(value['input_target_exact_guard']) is bool and value['input_target_exact_guard']==exact
        and (not exact or before==after),'VIEW_INPUT_TARGET_CHANGED_BEFORE_INPUT')
    return dict(stage=native['stage'],input_target_exact_guard=exact,
        native_AX_node_geometry_updated_after_read=before[0]!=after[0])

def effect(before,after,native_before,native_after,phase,plan,secondary=None):
    dispatch=before['dispatch_id'];geometry(before,dispatch,'before');geometry(after,dispatch,'after')
    ratio=calibrate(before,native_before,phase);require(calibrate(after,native_after,phase)==ratio,'VIEW_PIXEL_RATIO_CHANGED')
    require(before['window']==after['window'] and before['visual']==after['visual'] and before['viewport']==after['viewport']
        and native_before['webview']==native_after['webview'] and native_before['clip']==native_after['clip']
        and before['time_origin']==after['time_origin'] and before['observed_ms']<after['observed_ms'],'VIEW_OBSERVATION_CONTEXT_CHANGED')
    require(type(plan) is list and len(plan)==4,'VIEW_INPUT_PLAN_REFUSED')
    for value in plan:number(value)
    matrix=before['world']['matrix'];expected=list(matrix)
    if phase=='view-pan':
        require(abs(plan[2]-plan[0])==32 and abs(plan[3]-plan[1])==24,'VIEW_PAN_PLAN_REFUSED')
        expected[4]+=(plan[2]-plan[0])/ratio;expected[5]+=(plan[3]-plan[1])/ratio
    elif phase in ('view-pinch','view-takeover'):
        if phase=='view-pinch':
            require(abs(plan[2]-plan[0])>=80 and plan[1]==plan[3],'VIEW_PINCH_PLAN_REFUSED')
            start=[(plan[0]+plan[2])/2,plan[1]];end=start
        else:
            require(type(secondary) is list and len(secondary)==4,'VIEW_TAKEOVER_PLAN_REFUSED')
            for value in secondary:number(value)
            require(math.dist(plan[2:],secondary[:2])>=80
                and all(abs(actual-(base+(point-base)*1.1))<=.05 for actual,base,point in zip(secondary[2:],plan[2:],secondary[:2])),
                'VIEW_TAKEOVER_PLAN_REFUSED')
            start=[(plan[2]+secondary[0])/2,(plan[3]+secondary[1])/2]
            end=[(plan[2]+secondary[2])/2,(plan[3]+secondary[3])/2]
        viewport=before['viewport'];frame=native_before['webview']
        start=[(start[0]-frame[0])/ratio-viewport['left'],(start[1]-frame[1])/ratio-viewport['top']]
        end=[(end[0]-frame[0])/ratio-viewport['left'],(end[1]-frame[1])/ratio-viewport['top']]
        expected=[matrix[0]*1.1,0,0,matrix[3]*1.1,end[0]-(start[0]-matrix[4])*1.1,end[1]-(start[1]-matrix[5])*1.1]
    else:require(phase in ('view-cancel','view-background'),'VIEW_EFFECT_SCOPE')
    require(all(abs(a-b)<=.05 for a,b in zip(after['world']['matrix'],expected)),'VIEW_ORIGINAL_TRANSFORM_EFFECT_NOT_OBSERVED')
    return {'phase':phase,'dispatch_id':dispatch,'dpr':ratio,'before_matrix':matrix,'after_matrix':after['world']['matrix'],
        'actual_input_or_raw_preservation_proved_by_geometry_alone':False}
