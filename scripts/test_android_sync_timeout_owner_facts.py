"""Original read traces and real exclusive records; device results are fixtures."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

import android_sync_process_host as process
import android_sync_timeout_failure as failure
import android_sync_timeout_host as host
from test_android_sync_timeout_host import Harness,parent,ready

CODE='SYNC_PROCESS_OWNED_BOOT_ROOT_OR_DRIVER_CHANGED'


class OwnerFacts(unittest.TestCase):
    def boot(self,mode,observed):
        trace=[];subject=SimpleNamespace(identity={'boot':'owned'})
        child=SimpleNamespace(poll=lambda:trace.append('poll') or (0 if mode=='driver' else None))
        def identity(value):
            self.assertIs(value,subject);trace.append('identity')
            if mode=='identity-unknown':raise TimeoutError('private identity')
            return {'boot':'other' if mode=='identity' else 'owned'}
        def query(value,args,bound):
            self.assertIs(value,subject);self.assertEqual((args,bound),(('id','-u'),16));trace.append('root')
            if mode=='root-unknown':raise PermissionError('private root')
            return b'2000\n' if mode=='root' else b'0\n'
        error=None
        with patch.object(process,'_identity',side_effect=identity),patch.object(process,'_query',side_effect=query):
            try:process._boot_root(subject,child,observation=observed)
            except Exception as value:error=value
        return trace,error

    def test_each_composite_subcondition_keeps_original_short_circuit_and_guard(self):
        for mode,expected,trace in [('driver',[False,None,None],['poll']),
                ('identity',[True,False,None],['poll','identity']),('root',[True,True,False],['poll','identity','root'])]:
            with self.subTest(mode=mode):
                checks=[True,True,True];actual,error=self.boot(mode,checks)
                self.assertEqual(actual,trace);self.assertEqual(checks,expected);self.assertIs(type(error),RuntimeError)
                self.assertEqual(str(error),CODE);self.assertEqual(self.boot(mode,None)[0],trace)
        checks=[False,None,None];trace,error=self.boot('pass',checks)
        self.assertIsNone(error);self.assertEqual(checks,[True,True,True]);self.assertEqual(trace,self.boot('pass',None)[0])

    def test_unknown_observation_remains_null_and_never_becomes_a_false_fact(self):
        for mode,expected,kind in [('identity-unknown',[True,None,None],TimeoutError),
                ('root-unknown',[True,True,None],PermissionError)]:
            checks=[False,False,False];trace,error=self.boot(mode,checks)
            self.assertIs(type(error),kind);self.assertEqual(checks,expected)
            self.assertEqual(trace,self.boot(mode,None)[0])
            with self.assertRaises(RuntimeError):failure.owner_failure_observation(checks,parent(),ready())

    def test_owner_foreground_reads_follow_boot_only_and_optional_trace_is_equal(self):
        def execute(observed,alive):
            trace=[];identity={'boot':'owned'};subject=SimpleNamespace(identity=identity)
            child=SimpleNamespace(poll=lambda:trace.append('poll') or (None if alive else 0))
            def query(_,args,bound):
                trace.append(args);return {('id','-u'):b'0\n',('pidof',host.APP):b'123',('pm','list','packages','-U',host.APP):b'package'}[args]
            with patch.object(process,'_identity',side_effect=lambda _:trace.append('identity') or identity), \
                    patch.object(process,'_query',side_effect=query),patch.object(process,'product_uid',return_value=(12345,)), \
                    patch.object(process.ReleaseAuthBackground,'app_is_resumed',side_effect=lambda _:trace.append('resumed') or True), \
                    patch.object(process,'_reads',side_effect=lambda x:x), \
                    patch.object(process,'process_lifetime',side_effect=lambda *a:trace.append('lifetime') or {'ticks':7}):
                try:process._owner(subject,child,b'123',12345,{'ticks':7},boot_root_observation=observed)
                except RuntimeError as error:self.assertEqual(str(error),CODE)
            return trace
        self.assertEqual(execute(None,True),execute([None]*3,True))
        self.assertEqual(execute(None,False),['poll']);self.assertEqual(execute([None]*3,False),['poll'])

    def test_closed_typed_projection_binds_original_context_without_raw_values(self):
        for index,checks in enumerate(([False,None,None],[True,False,None],[True,True,False])):
            value=failure.owner_failure_observation(list(checks),parent(),ready())
            self.assertEqual(value['check_results'],list(checks));self.assertEqual(value['first_failed_check'],value['check_order'][index])
            self.assertFalse(value['whole_timeout_component_passed']);self.assertFalse(value['native_timeout_verified'])
            self.assertFalse(value['raw_identity_or_root_output_recorded']);self.assertLess(len(json.dumps(value)),2048)
        for checks in ([0,None,None],[True,0,None],[True,True,0],[True,True,True],[None]*3,[False,False,None],[True,None,False]):
            with self.subTest(checks=checks),self.assertRaises(RuntimeError):failure.owner_failure_observation(checks,parent(),ready())
        for key,value in [('uid',True),('window',True),('elapsed_ms',True),('driver_intent_id',parent()['parent_intent_id'])]:
            bad=dict(ready(),**{key:value})
            with self.subTest(key=key),self.assertRaises(RuntimeError):failure.owner_failure_observation([False,None,None],parent(),bad)
        with self.assertRaises(RuntimeError):failure.owner_failure_observation([False,None,None],dict(parent(),private='secret'),ready())

    def test_real_exclusive_record_rejects_stale_scope_and_preserves_partial_original(self):
        with tempfile.TemporaryDirectory(prefix='onx-timeout-owner-facts-') as folder:
            subject=SimpleNamespace(output=Path(folder));p,r=parent(),ready();cached=(deepcopy(p),deepcopy(r),[True,False,None])
            for bad in [None,(dict(p,input_intent_id=p['parent_intent_id']),r,cached[2]),(p,dict(r,window=5),cached[2])]:
                with self.assertRaises(RuntimeError):failure.retain_owner_failure(subject,'original',p,r,bad,RuntimeError(CODE))
            path=Path(folder)/'original-host-owner-failure-observation.json';self.assertFalse(path.exists())
            failure.retain_owner_failure(subject,'original',p,r,cached,RuntimeError(CODE));before=path.read_bytes()
            self.assertEqual(json.loads(before)['first_failed_check'],'same_original_owned_identity')
            with self.assertRaises(FileExistsError):failure.retain_owner_failure(subject,'original',p,r,cached,RuntimeError(CODE))
            self.assertEqual(before,path.read_bytes());path.write_bytes(b'{"schema":')
            with self.assertRaises(FileExistsError):failure.retain_owner_failure(subject,'original',p,r,cached,RuntimeError(CODE))
            self.assertEqual(path.read_bytes(),b'{"schema":')

    def test_noncomposite_or_unknown_cached_outcome_is_never_serialized(self):
        with tempfile.TemporaryDirectory(prefix='onx-timeout-owner-unknown-') as folder:
            subject=SimpleNamespace(output=Path(folder));p,r=parent(),ready()
            for error,checks in [(RuntimeError('private'),[False,None,None]),(TimeoutError(CODE),[False,None,None]),
                    (RuntimeError(CODE),[True,None,None]),(RuntimeError(CODE),[True,True,True])]:
                with self.assertRaises(RuntimeError):failure.retain_owner_failure(subject,'original',p,r,(p,r,checks),error)
            self.assertEqual(list(Path(folder).iterdir()),[])

    def test_retained_original_reader_refuses_partial_private_and_mistyped_records(self):
        p,r=parent(),ready();value=failure.owner_failure_observation([True,True,False],p,r)
        self.assertEqual(failure.validate_owner_failure(json.loads(json.dumps(value)),p,r),value)
        for key,new in [('schema',True),('uid',True),('first_failed_check','original_driver_alive'),
                ('ready_metadata_sha256','0'*64),('check_order',list(reversed(value['check_order']))),
                ('check_results',[True,None,False]),('whole_timeout_component_passed',True)]:
            with self.subTest(key=key),self.assertRaises(RuntimeError):failure.validate_owner_failure(dict(value,**{key:new}),p,r)
        for bad in [None,{},dict(value,private='secret'),{k:v for k,v in value.items() if k!='guard_code'}]:
            with self.assertRaises(RuntimeError):failure.validate_owner_failure(bad,p,r)
        with self.assertRaises(RuntimeError):failure.validate_owner_failure(value,p,dict(r,window=5))
        with self.assertRaises(RuntimeError):failure.closed_json(b'{"schema":1,"schema":1}')

    def test_actual_timeout_host_freezes_false_then_records_same_driver_rejection(self):
        with tempfile.TemporaryDirectory(prefix='onx-timeout-owner-host-') as folder:
            h=Harness(folder);h.variant='driver-exit-before-release';original=host.retain_owner_failure;calls=[]
            def retain(*args):
                self.assertTrue((h.subject.output/'original-host-failure.json').exists());calls.append(args)
                return original(*args)
            with patch.object(host,'retain_owner_failure',side_effect=retain),self.assertRaisesRegex(RuntimeError,'^'+CODE+'$'):h.execute()
            self.assertEqual(len(calls),1);value=json.loads((h.subject.output/'original-host-owner-failure-observation.json').read_bytes())
            self.assertEqual(value['check_results'],[False,None,None]);self.assertEqual(value['window'],4)
            self.assertEqual(h.events,['launch','tap','held','input-finish']);self.assertFalse(h.held.release_attempted)
            self.assertFalse(h.subject.phases);self.assertEqual(h.subject.stop.call_count,1)

    def test_actual_timeout_host_retains_identity_and_root_failures_without_requery(self):
        for mode,expected in [('identity',[True,False,None]),('root',[True,True,False])]:
            with self.subTest(mode=mode),tempfile.TemporaryDirectory(prefix='onx-timeout-owner-prefix-') as folder:
                h=Harness(folder);original=h.subject.shell;identity=h.subject.observer.observe_identity;armed=False;root_reads=0
                def shell(*args,**kwargs):
                    nonlocal armed,root_reads
                    if armed and args==('id','-u'):
                        root_reads+=1
                        if mode=='root':return SimpleNamespace(returncode=0,stdout=b'2000\n',stderr=b'')
                    result=original(*args,**kwargs)
                    if args[:2]==('run-as',host.DRIVER) and args[-1].endswith('-failed-ready.json'):armed=True
                    return result
                def observe():
                    value=identity()
                    return dict(value,boot_id=parent()['input_intent_id']) if armed and mode=='identity' else value
                h.subject.shell=shell;h.subject.observer.observe_identity=observe
                with self.assertRaisesRegex(RuntimeError,'^'+CODE+'$'):h.execute()
                value=json.loads((h.subject.output/'original-host-owner-failure-observation.json').read_bytes())
                self.assertEqual(value['check_results'],expected);self.assertEqual(root_reads,1 if mode=='root' else 0)
                self.assertEqual(h.events,['launch','tap','held','input-finish']);self.assertFalse(h.held.release_attempted)

    def test_unknown_partial_metadata_write_keeps_original_exception_and_is_not_retried(self):
        with tempfile.TemporaryDirectory(prefix='onx-timeout-owner-write-') as folder:
            h=Harness(folder);h.variant='driver-exit-before-release';original=failure.record;attempts=[]
            def record(path,value):
                if path.name.endswith('-host-owner-failure-observation.json'):
                    attempts.append(path);path.write_bytes(b'{"schema":');raise PermissionError('private write')
                return original(path,value)
            with patch.object(failure,'record',side_effect=record),self.assertRaisesRegex(RuntimeError,'^'+CODE+'$'):h.execute()
            self.assertEqual(len(attempts),1);self.assertEqual(attempts[0].read_bytes(),b'{"schema":')
            self.assertTrue((h.subject.output/'original-driver-failure-observation.json').exists())
            self.assertFalse(h.held.release_attempted);self.assertFalse(h.subject.phases)


if __name__=='__main__':unittest.main()
