from copy import deepcopy
import json,unittest
import android_external_contract as c

class OriginalExternalRecord(unittest.TestCase):
    def record(self,key='markdown'):
        original=c.armed(key,'af17f3b7-e030-4b52-9734-5409033de001')
        value=dict(schema=1,scope='external-links',key=key,operation_id=original['operation_id'],original_uri=original['original_uri'],
            actual_action='android.intent.action.VIEW',actual_browsable=True,actual_data_uri=original['expected_delivered_uri'],
            receiver_package=c.DRIVER,elapsed_realtime_ms=1234,browser_or_network_or_physical_verified=False)
        return original,value

    def test_each_owned_record_binds_the_original_action_and_exact_uri(self):
        for key in ('markdown','canvas'):
            original,value=self.record(key);self.assertEqual(c.validate_record(original,json.dumps(value).encode()),value)
        self.assertTrue(c.CANVAS.startswith('HTTPS:'));self.assertTrue(c.CANVAS_DELIVERED.startswith('https:'))
        self.assertEqual(c.CANVAS[5:],c.CANVAS_DELIVERED[5:])

    def test_altered_or_foreign_fields_do_not_become_a_successful_handoff(self):
        original,value=self.record()
        for field,wrong in [('schema',True),('scope','other'),('key','canvas'),('operation_id','other'),('original_uri',c.CANVAS),
            ('actual_action','android.intent.action.SEND'),('actual_browsable',False),('actual_data_uri',c.MD+'extra'),
            ('receiver_package','other.application'),('elapsed_realtime_ms',True),('elapsed_realtime_ms',-1),
            ('elapsed_realtime_ms',2**63),('browser_or_network_or_physical_verified',True)]:
            changed=deepcopy(value);changed[field]=wrong
            with self.subTest(field=field,wrong=wrong),self.assertRaises(RuntimeError):c.validate_record(original,json.dumps(changed).encode())

    def test_missing_extra_duplicate_or_oversize_records_are_refused(self):
        original,value=self.record()
        for changed in ({k:v for k,v in value.items() if k!='actual_data_uri'},dict(value,extra='unreviewed')):
            with self.assertRaises(RuntimeError):c.validate_record(original,json.dumps(changed).encode())
        raw=json.dumps(value).encode()
        with self.assertRaises(RuntimeError):c.validate_record(original,raw[:-1]+b',"actual_browsable":true}')
        for raw in (b'',b'x'*8193):
            with self.assertRaises(RuntimeError):c.validate_record(original,raw)

    def test_armed_intent_is_closed_and_never_reinterprets_the_original_scheme(self):
        original,value=self.record('canvas')
        for changed in (dict(original,extra=True),dict(original,schema=True),dict(original,original_uri=c.CANVAS_DELIVERED),dict(original,operation_id='other')):
            with self.assertRaises(RuntimeError):c.validate_record(changed,json.dumps(value).encode())
        with self.assertRaises(RuntimeError):c.armed('foreign','af17f3b7-e030-4b52-9734-5409033de001')

    def test_declared_canvas_fixture_and_source_draft_preserve_original_uri_text(self):
        raw=c.canvas_fixture();value=json.loads(raw)
        self.assertEqual(value['nodes'][0]['url'],c.CANVAS);self.assertIn('vendor',value['nodes'][0])
        self.assertIn('vendor-root',value);self.assertIn(b'\r\n',raw);self.assertNotIn(b'\n',raw.replace(b'\r\n',b''))
        self.assertTrue(c.DRAFT.startswith(c.BODY));self.assertIn(c.MD,c.DRAFT)
        self.assertEqual(len([s for s in c.STEPS if s.endswith('-seed')]),2)

if __name__=='__main__':unittest.main()
