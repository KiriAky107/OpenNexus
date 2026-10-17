"""Pure retained-original validation; actual cold UI evidence is separate."""
from copy import deepcopy
import unittest
import uuid

from android_sync_conflict import IDS, validate_cold_original_read


class ColdOriginals(unittest.TestCase):
    def setUp(self):
        self.decision = {key:str(uuid.uuid4()) for key in IDS}
        self.decision.update(sequence=2,state='completed',fingerprint='a'*64)
        self.before = {'binding':{'cursor':3},'decisions':[self.decision],
                       'jobs':[{'operation_id':self.decision['copy_id'],'state':'acked'}]}
        self.seen = {'sequence':2,'original_native_ids':[self.decision[key] for key in IDS],
                     'read_only_original_query':True,'cold_original_list_entry':True,
                     'native_operations_created_by_driver':False}

    def test_ui_ids_match_the_same_completed_native_record_without_changing_snapshots(self):
        original=deepcopy(self.before)
        validate_cold_original_read(self.before,deepcopy(self.before),self.decision,self.seen)
        self.assertEqual(self.before,original)

    def test_replaced_ids_digest_sequence_or_any_durable_record_change_are_rejected(self):
        for key in ['binding','decisions','jobs']:
            after=deepcopy(self.before)
            if key=='binding':after[key]['cursor']=4
            elif key=='decisions':after[key][0]['fingerprint']='b'*64
            else:after[key][0]['operation_id']=str(uuid.uuid4())
            with self.subTest(key=key),self.assertRaises(RuntimeError):
                validate_cold_original_read(self.before,after,self.decision,self.seen)
        for field,value in [('sequence',3),('original_native_ids',[str(uuid.uuid4()) for _ in IDS]),
                            ('native_operations_created_by_driver',True),('read_only_original_query',False)]:
            seen=deepcopy(self.seen);seen[field]=value
            with self.subTest(field=field),self.assertRaises(RuntimeError):
                validate_cold_original_read(self.before,deepcopy(self.before),self.decision,seen)


if __name__=='__main__':unittest.main(verbosity=2)
