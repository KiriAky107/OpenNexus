import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import yaml
from android_canvas_motion_probe import A, B, E, APP, FIXTURE, PATH, STEPS, graph, encoded, changed_node, readonly, touch_result, actual_input_result, run_canvas_motion
from android_runtime_probe import Probe
from android_canvas_graph_probe import saved_original
from test_android_desktop_workflow import admitted


class OriginalMotion(unittest.TestCase):
    def test_shared_save_guard_binds_the_new_explicit_path_and_keeps_old_default_path_rejection(self):
        raw=FIXTURE;digest=hashlib.sha256(raw).hexdigest()
        result=dict(file_id=A,path=PATH,hash=digest,revision=1,deleted=False)
        after=dict(files=[dict(id=A,path=PATH,hash=digest,revision=1,deleted=0)],
            drafts=[dict(file_id=A,content=raw.decode(),generation=1,saved_generation=1)],
            saves=[dict(operation_id=E,state='committed',error=None,path=PATH,content=raw.decode(),file_id=None)],
            operations=[dict(operation_id=E,state='committed',fingerprint='a'*64,result=json.dumps(result))],
            outbox=[dict(operation_id=E,file_id=A,revision=1,path=PATH,hash=digest,operation='put',state='pending')])
        self.assertEqual(saved_original(None,after,raw,path=PATH)['file_id'],A)
        with self.assertRaises(RuntimeError):saved_original(None,after,raw)
        changed=copy.deepcopy(after);changed['saves'][0]['path']='foreign.canvas'
        with self.assertRaises(RuntimeError):saved_original(None,changed,raw,path=PATH)

    def test_declared_geometry_changes_preserve_other_nodes_edge_extensions_and_original_text(self):
        self.assertTrue(FIXTURE.endswith(b'\r\n'))
        value=graph(FIXTURE);value['nodes'][0].update(x=80,y=75)
        changed_node(FIXTURE,encoded(value),{'x':80,'y':75})
        moved=encoded(value);value['nodes'][0].update(width=200,height=175)
        changed_node(moved,encoded(value),{'width':200,'height':175})
        for mutate in [lambda v:v['nodes'][0].update(text='wrong'),lambda v:v['nodes'][1].update(x=331),
                lambda v:v['edges'][0].update(label='wrong'),lambda v:v['vendor-root'].update(original=[]),
                lambda v:v['nodes'][0].update(id='foreign')]:
            wrong=graph(FIXTURE);wrong['nodes'][0].update(x=80,y=75);mutate(wrong)
            with self.assertRaises(RuntimeError):changed_node(FIXTURE,encoded(wrong),{'x':80,'y':75})
        for changes in ({'x':True,'y':75},{'text':'unknown'},{'x':80,'height':175}):
            with self.assertRaises(RuntimeError):changed_node(FIXTURE,FIXTURE,changes)

    def test_readonly_interruption_preserves_exact_draft_native_ledger_and_raw_cold_allows_only_new_edit_session(self):
        before=dict(files=[{'id':'original'}],drafts=[{'edit_session':'old','generation':2,'saved_generation':2}],
            saves=[{'id':'original'}],outbox=[{'id':'original'}],operations=[{'id':'original'}],journal=[])
        readonly(before,copy.deepcopy(before),FIXTURE,FIXTURE)
        after=copy.deepcopy(before);after['drafts'][0]['edit_session']='new'
        readonly(before,after,FIXTURE,FIXTURE,cold=True)
        with self.assertRaises(RuntimeError):readonly(before,before,FIXTURE,FIXTURE,cold=True)
        for key in ('files','drafts','saves','outbox','operations','journal'):
            wrong=copy.deepcopy(before);wrong[key].append({'unexpected':True})
            with self.assertRaises(RuntimeError):readonly(before,wrong,FIXTURE,FIXTURE)
        with self.assertRaises(RuntimeError):readonly(before,before,FIXTURE,FIXTURE+b' ')
        after['drafts'][0]['generation']=3
        with self.assertRaises(RuntimeError):readonly(before,after,FIXTURE,FIXTURE,cold=True)

    def trace(self, step):
        phase='motion-'+step;node=[100,100,420,380];control=[332,292,420,380]
        plan=[160.,160.,240.,230.]
        if step=='resize':plan=[376.,336.,456.,406.]
        if step=='pan':plan=[400.,800.,432.,824.]
        if step=='pinch':plan=[400.,800.,600.,800.]
        intent=dict(schema=1,scope='canvas-motion',phase=phase,owner=APP,node_bounds=node,clip=[0,0,1000,1000],
            controls=[control],obstacles=[node],plan=plan)
        if step=='resize':intent['resize_control']=control
        if step=='background':intent['resume_activity']=APP+'/.MainActivity'
        result=dict(schema=1,phase=phase,owner=APP,before_node_bounds=node,after_node_bounds=list(node),
            events=[],background_seen=step=='background',resumed_owned=step=='background',owned_root_after=True)
        def event(action, points):
            result['events'].append(dict(action=action,index=1 if action in (5,6) else 0,source=4098,
                ids=list(range(len(points))),tools=[1]*len(points),points=copy.deepcopy(points),
                down_ms=1000,time_ms=1000+len(result['events'])*35))
        event(0,[plan[:2]])
        if step!='pinch':
            for i in range(1,9):event(2,[[plan[0]+(plan[2]-plan[0])*i/8,plan[1]+(plan[3]-plan[1])*i/8]])
        if step in ('pinch','takeover'):
            if step=='pinch':
                first,second,end_first,end_second=[400.,800.],[600.,800.],[390.,800.],[610.,800.]
                result['after_node_bounds']=[60,30,412,338]
            else:
                first,second,end_first,end_second=plan[2:],[600.,800.],plan[2:],[636.,857.]
                intent['secondary']=second+end_second;result['after_node_bounds']=[86,87,438,395]
            event(5,[first,second])
            for i in range(1,9):event(2,[[a+(b-a)*i/8 for a,b in zip(first,end_first)],
                [a+(b-a)*i/8 for a,b in zip(second,end_second)]])
            event(6,[end_first,end_second]);last=[end_first[0]+8,end_first[1]+8]
            event(2,[last]);event(1,[last])
        elif step!='background':event(3 if step=='cancel' else 1,[plan[2:]])
        if step=='pan':result['after_node_bounds']=[132,124,452,404]
        return intent,result,phase

    def test_exact_original_finger_traces_include_real_cancel_pinch_takeover_and_Home_without_artificial_cancel(self):
        for step in ('move','resize','pan','pinch','cancel','takeover','background'):
            with self.subTest(step=step):self.assertEqual(touch_result(*self.trace(step))['phase'],'motion-'+step)
        background=self.trace('background')[1]
        self.assertNotIn(1,[v['action'] for v in background['events']])
        self.assertNotIn(3,[v['action'] for v in background['events']])

    def test_touch_identity_order_original_coordinates_foreign_windows_or_suppressed_pinch_are_rejected(self):
        for mutate in [lambda i,r:r.update(owner='other.app'),lambda i,r:i.update(owner='other.app'),
                lambda i,r:r['events'][0].update(ids=[1]),lambda i,r:r['events'][0].update(tools=[0]),
                lambda i,r:r['events'][0].update(source=0),lambda i,r:r['events'][2].update(time_ms=1),
                lambda i,r:r['events'][3]['points'][0].__setitem__(0,999),
                lambda i,r:r['events'][-1].update(action=1),lambda i,r:r.update(owned_root_after=False),
                lambda i,r:r.update(after_node_bounds=[180,170,500,450])]:
            i,r,phase=self.trace('cancel');mutate(i,r)
            with self.assertRaises(RuntimeError):touch_result(i,r,phase)
        for step in ('pinch','takeover'):
            i,r,phase=self.trace(step);r['after_node_bounds']=r['before_node_bounds']
            with self.assertRaises(RuntimeError):touch_result(i,r,phase)
        i,r,phase=self.trace('background');r['background_seen']=False
        with self.assertRaises(RuntimeError):touch_result(i,r,phase)
        i,r,phase=self.trace('pinch');r['events'][1]['index']=0
        with self.assertRaises(RuntimeError):touch_result(i,r,phase)

    def test_no_child_button_or_clipped_node_can_be_substituted_for_original_drag_or_resize(self):
        i,r,phase=self.trace('move');i['controls'].append([150,150,170,170])
        with self.assertRaises(RuntimeError):touch_result(i,r,phase)
        i,r,phase=self.trace('resize');i['controls']=[]
        with self.assertRaises(RuntimeError):touch_result(i,r,phase)
        i,r,phase=self.trace('move');i['clip']=[110,110,1000,1000]
        with self.assertRaises(RuntimeError):touch_result(i,r,phase)
        i,r,phase=self.trace('pan');i['obstacles'].append([390,790,450,850])
        with self.assertRaises(RuntimeError):touch_result(i,r,phase)

    def test_actual_input_check_does_not_relax_the_original_strict_effect_comparator(self):
        intent,result,phase=self.trace('pan');result['after_node_bounds']=[128,124,452,404]
        self.assertEqual(actual_input_result(intent,result,phase)['events'],10)
        with self.assertRaisesRegex(RuntimeError,'MOTION_ACTUAL_PAN_NOT_OBSERVED'):touch_result(intent,result,phase)
        intent['scope']='canvas-view';intent['phase']=result['phase']='view-pan'
        self.assertEqual(actual_input_result(intent,result,'view-pan')['events'],10)
        with self.assertRaises(RuntimeError):touch_result(intent,result,'view-pan')

    def test_scope_runs_only_pending_motion_and_reuses_fixed_APK_without_replaying_other_scenes(self):
        self.assertEqual(len(STEPS),11)
        probe=Probe.__new__(Probe);probe.args=SimpleNamespace(scope='canvas-motion');probe.boot=Mock()
        with patch('android_canvas_motion_probe.run_canvas_motion') as run:probe.run()
        probe.boot.assert_called_once_with();run.assert_called_once_with(probe)
        probe.native_keystore_tests()  # No binding field: any repeated Native test access would fail.
        workflow=yaml.load((Path(__file__).resolve().parents[1]/'.github/workflows/android.yml').read_text('utf-8'),Loader=yaml.BaseLoader)
        for reuse in ('','39112675356'):self.assertFalse(admitted(workflow['jobs']['debug-apk']['if'],'canvas-motion',reuse=reuse))
        self.assertTrue(admitted(workflow['jobs']['emulator-runtime']['if'],'canvas-motion',reuse='39112675356',build='skipped'))
        self.assertFalse(admitted(workflow['jobs']['emulator-runtime']['if'],'canvas-motion',reuse='',build='skipped'))
        self.assertFalse(admitted(workflow['jobs']['desktop-startup']['if'],'canvas-motion'))
        self.assertIn('canvas-motion',workflow['on']['workflow_dispatch']['inputs']['runtime_scope']['options'])

    def test_local_or_phone_scope_is_refused_before_GUI_or_files(self):
        with tempfile.TemporaryDirectory() as directory,patch.dict(os.environ,{},clear=True):
            probe=SimpleNamespace(args=SimpleNamespace(scope='canvas-motion',serial='user-phone'),output=Path(directory))
            with self.assertRaises(RuntimeError):run_canvas_motion(probe)
            self.assertEqual(list(Path(directory).iterdir()),[])


if __name__=='__main__':unittest.main()
