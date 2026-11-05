"""New synthetic pre-cycle capture/SQL profile; controlled admission, no live UI."""
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch

import android_release_sync_quota_collect as collect
import android_release_sync_quota_snapshot as snapshots
import android_release_sync_quota_validation as quota
from test_android_release_sync_quota_collect import task
from test_android_release_sync_quota_snapshot import retained, mutate

COLLECTION = '00000000-0000-0000-0000-000000009100'
DIGEST = 'a'*64


class Unknown:
    def __str__(self):raise AssertionError('Unknown formatted')
    def __repr__(self):raise AssertionError('Unknown represented')
    def __eq__(self,other):raise AssertionError('Unknown compared')
    def __hash__(self):raise AssertionError('Unknown hashed')


class AssociationFixtureAdmission(collect.OriginalQuotaAssociationAdmission):
    """Controlled gate report only; no actual associated-driver/stop acceptance."""
    def __init__(self,base,identity):
        self.base,self.identity = dict(base),dict(identity)
        self.calls = 0;self.result = DIGEST;self.error = None

    def verify(self,*,association_scope,identity,collection_id):
        self.calls += 1
        assert association_scope == self.base and identity == self.identity and collection_id == COLLECTION
        if self.error is not None:raise self.error
        association_scope['uid'] = 1;identity['boot_id'] = 'changed-copy'
        return self.result


@contextmanager
def precycle_task(*,wal=False):
    # Reuse only synthetic fixture functions, never any old test method or ZIP.
    with task('associated',wal=wal) as (root,device,_,legacy,scope,selected,expected):
        base = {k:v for k,v in scope.items() if k not in ('parent_id','input_id','window')}
        gate = AssociationFixtureAdmission(base,device.identity)
        before = deepcopy(expected);del before['scope']
        before.update(schema=2,association_scope=dict(base),stage=quota.ASSOCIATED_BEFORE_CYCLE)
        def invoke(**changes):
            args = dict(association_scope=base,identity=device.identity,collection_id=COLLECTION,admission=gate)
            args.update(changes)
            return collect.collect_associated_before_cycle(device,'emulator-5562',root/'tools',root/'artifact',**args)
        yield root,device,gate,invoke,base,scope,selected,before,legacy


class PrecycleAssociated(unittest.TestCase):
    def project(self,root,output,base,selected):
        return snapshots.project_associated_before_cycle(output,root/'working',association_scope=base,selected=selected)

    def test_actual_collector_writer_and_readonly_SQL_without_future_cycle_ids(self):
        with precycle_task() as (root,device,gate,invoke,base,_,selected,expected,_):
            output = invoke();raw = (output/'snapshot.json').read_bytes();original = retained(output)
            manifest = json.loads(raw)
            self.assertEqual(gate.calls,1);self.assertEqual(device.stops,3)
            self.assertEqual(manifest['schema'],2);self.assertNotIn('quota_scope',manifest)
            self.assertEqual(manifest['quota_association_scope'],base)
            self.assertTrue({'parent_id','input_id','window'}.isdisjoint(manifest['quota_association_scope']))
            self.assertEqual(manifest['collection_id'],COLLECTION)
            self.assertEqual(manifest['admission_metadata_canonical_sha256'],DIGEST)
            self.assertEqual(len(device.heads),len(set(device.heads)))
            connect = sqlite3.connect;paths = []
            def tracked(path,*args,**kwargs):
                self.assertTrue(path.startswith((root/'working').as_uri()+'/') and path.endswith('?mode=ro'))
                self.assertNotIn(output.as_uri(),path);paths.append(path)
                return connect(path,*args,**kwargs)
            with patch.object(snapshots.sqlite3,'connect',side_effect=tracked), \
                 patch.object(quota,'_before',side_effect=AssertionError('Fake full scope route')):
                result = self.project(root,output,base,selected)
            self.assertEqual(len(paths),2);self.assertEqual(result.projection,expected)
            self.assertIsNone(result.projection['save']['file_id'])
            self.assertEqual([x['state'] for x in result.projection['outbox']],['archived','pending'])
            self.assertEqual(result.note,result.spool);self.assertEqual(result.note,quota.BODY)
            self.assertEqual(result.evidence['original_manifest_raw_sha256'],hashlib.sha256(raw).hexdigest())
            for key in ('runtime_verified','completed_driver_and_stop_gate_verified','credentials_selected',
                        'original_cycle_ids_present_at_capture','later_cycle_binding_verified'):
                self.assertIs(result.evidence[key],False)
            self.assertEqual(retained(output),original)
            self.assertNotIn(selected['endpoint'],json.dumps(result.projection))
            self.assertFalse(list((root/'artifact').iterdir()))

    def test_full_scope_future_null_and_unknown_base_reject_before_gate_or_device(self):
        with precycle_task() as (_,device,gate,invoke,base,scope,_,_,_):
            bads = [scope,dict(base,parent_id=None),dict(base,window=None),dict(base,private=Unknown()),
                    dict(base,uid=True),dict(base,uid=100000),dict(base,owner_run=Unknown())]
            for value in bads:
                with self.assertRaises((RuntimeError,ValueError)):invoke(association_scope=value)
            self.assertEqual(gate.calls,0);self.assertEqual(device.calls,[])

    def test_no_boolean_or_legacy_gate_and_no_unknown_digest_success(self):
        with precycle_task() as (_,device,gate,invoke,_,_,_,_,_):
            for value in (None,True,dict(verified=True)):
                with self.assertRaises(RuntimeError):invoke(admission=value)
            for value in (None,True,Unknown(),'b'*63):
                gate.result = value
                with self.assertRaises((RuntimeError,ValueError)):invoke()
            self.assertEqual(device.calls,[])

    def test_gate_exception_and_collection_collision_stop_before_any_copy(self):
        with precycle_task() as (_,device,gate,invoke,base,_,_,_,_):
            for value in (base['save_operation_id'],base['boot_id'],Unknown()):
                with self.assertRaises((RuntimeError,ValueError)):invoke(collection_id=value)
            self.assertEqual(gate.calls,0)
            error = OSError('synthetic original gate unavailable');gate.error = error
            with self.assertRaises(OSError) as caught:invoke()
            self.assertIs(caught.exception,error);self.assertEqual(device.calls,[])

    def test_legacy_full_scope_profiles_are_not_upgraded_or_accepted_as_precycle(self):
        with precycle_task() as (root,_,_,_,base,scope,selected,_,legacy):
            output = legacy();original = retained(output)
            with self.assertRaises(RuntimeError):self.project(root,output,base,selected)
            self.assertFalse((root/'working').exists());self.assertEqual(retained(output),original)
            self.assertEqual(json.loads((output/'snapshot.json').read_bytes())['schema'],1)
        with precycle_task() as (root,_,_,invoke,base,scope,selected,_,_):
            output = invoke()
            with self.assertRaises(RuntimeError):snapshots.project_quota(output,root/'working',scope=scope,selected=selected,stage='associated')
            self.assertFalse((root/'working').exists())

    def test_schema2_manifest_private_partial_duplicate_and_typed_metadata_refuse(self):
        for kind in ('private','partial','duplicate','bool','digest','collection','future','stage'):
            with self.subTest(kind=kind),precycle_task() as (root,_,_,invoke,base,_,selected,_,_):
                output = invoke();f = output/'snapshot.json';value = json.loads(f.read_bytes())
                if kind == 'private':value['private_UI'] = 'synthetic-private'
                if kind == 'bool':value['schema'] = True
                if kind == 'digest':value['admission_metadata_canonical_sha256'] = None
                if kind == 'collection':value['collection_id'] = base['association_operation_id']
                if kind == 'future':value['quota_association_scope']['parent_id'] = None
                if kind == 'stage':value['quota_stage'] = 'quota-failed'
                raw = json.dumps(value)
                if kind == 'duplicate':raw = raw.replace('"schema": 2','"schema": 2, "schema": 2')
                if kind == 'partial':raw = raw[:-1]
                f.write_text(raw,encoding='utf-8');original = retained(output)
                with self.assertRaises(RuntimeError):self.project(root,output,base,selected)
                self.assertFalse((root/'working').exists());self.assertEqual(retained(output),original)

    def test_base_projection_still_requires_SaveNULL_original_archive_and_empty_attempts(self):
        for table,sql,draft in [
            ('save_intents',"UPDATE save_intents SET file_id='replacement'",True),
            ('outbox',"UPDATE outbox SET state='acked' WHERE state='archived'",False),
            ('outbox',"UPDATE outbox SET operation_id='replacement' WHERE state='pending'",False),
            ('sync_attempts',"INSERT INTO sync_attempts (binding,operation_id,attempts,outcome,error) VALUES ('binding','operation',1,'failed','QUOTA_EXCEEDED')",False)]:
            with self.subTest(table=table),precycle_task() as (root,_,_,invoke,base,_,selected,_,_):
                output = invoke();mutate(output,table,sql,draft=draft);original = retained(output)
                with self.assertRaises(RuntimeError):self.project(root,output,base,selected)
                self.assertEqual(retained(output),original)

    def test_wrong_note_or_spool_and_running_owned_product_never_form_manifest(self):
        for kind in ('note','spool','running'):
            with self.subTest(kind=kind),precycle_task() as (root,device,_,invoke,_,_,_,_,_):
                if kind == 'note':device.files[snapshots.NOTE] = quota.BODY+b' '
                if kind == 'spool':device.files[snapshots.SPOOL_SOURCE] = b'x'*56
                if kind == 'running':device.running_at = 2
                with self.assertRaises(RuntimeError):invoke()
                self.assertFalse((root/'tools'/('quota-original-'+quota.ASSOCIATED_BEFORE_CYCLE)/'snapshot/snapshot.json').exists())

    def test_real_WAL_inputs_and_raw_manifest_remain_distinct_from_typed_projection(self):
        with precycle_task(wal=True) as (root,_,_,invoke,base,_,selected,expected,_):
            output = invoke();before = retained(output)
            self.assertTrue(all((output/(n+'-wal')).exists() for n in snapshots.DATABASES))
            result = self.project(root,output,base,selected)
            self.assertEqual(result.projection,expected);self.assertEqual(retained(output),before)
            self.assertNotEqual(result.evidence['original_manifest_raw_sha256'],hashlib.sha256(json.dumps(result.projection,sort_keys=True).encode()).hexdigest())
            self.assertEqual(result.evidence['admission_metadata_canonical_sha256'],DIGEST)

    def test_closed_base_rows_and_returns_do_not_alias_caller_arrays_or_coerce_unknown(self):
        with precycle_task() as (_,_,_,_,base,_,_,before,_):
            result = quota.before_associated_capture(before,base)
            before['outbox'][0]['state'] = 'changed'
            base['uid'] = 1
            self.assertEqual(result['outbox'][0]['state'],'archived')
            self.assertEqual(result['association_scope']['uid'],10216)
            for value in (Unknown(),None,True):
                bad = deepcopy(result);bad['association']['original_request_bytes_sha256'] = value
                with self.assertRaises(ValueError):quota.before_associated_capture(bad,result['association_scope'])

    def test_partial_existing_capture_is_not_overwritten_or_retried(self):
        with precycle_task() as (root,device,_,invoke,_,_,_,_,_):
            output = invoke();original = retained(output);count = len(device.calls)
            with self.assertRaisesRegex(RuntimeError,'^RELEASE_OBSERVATION_ORIGINAL_OUTPUT_EXISTS$'):invoke()
            self.assertEqual(len(device.calls),count)
            self.assertEqual(retained(output),original)


if __name__ == '__main__':unittest.main()
