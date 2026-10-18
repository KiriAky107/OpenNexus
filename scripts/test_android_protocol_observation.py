from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import json,os,tempfile,unittest
from android_protocol_observation import protocol_summary,observe_owned_protocol

class ProtocolObservation(unittest.TestCase):
    def protocol(self):return {'domains':[{'domain':'Debugger','commands':[{'name':'enable'},{'name':'getScriptSource','parameters':[{'name':'scriptId'}]}],'events':[{'name':'scriptParsed'}]}]}
    def test_summary_has_only_bounded_numeric_and_boolean_capabilities(self):
        value=self.protocol();value['private']='SECRET_PROTOCOL_BODY';r=protocol_summary(value)
        self.assertEqual(r,{'domains_array':True,'domain_count':1,'Debugger_domains':1,'enable_commands':1,'getScriptSource_commands':1,'scriptId_required':True,'scriptParsed_events':1})
        self.assertNotIn('SECRET',json.dumps(r))
    def test_missing_domain_and_optional_parameter_are_visible_not_admitted(self):
        self.assertEqual(protocol_summary({'domains':[]})['Debugger_domains'],0)
        v=self.protocol();v['domains'][0]['commands'][1]['parameters'][0]['optional']=True
        self.assertFalse(protocol_summary(v)['scriptId_required'])
    def test_ambiguous_and_oversize_domain_arrays_are_counted_without_body_export(self):
        v=self.protocol();v['domains']*=3;self.assertEqual(protocol_summary(v)['Debugger_domains'],2)
        r=protocol_summary({'domains':[{'domain':'SECRET'}]*257});self.assertEqual(r['domain_count'],257);self.assertEqual(r['Debugger_domains'],0)
    def test_malformed_root_returns_only_the_safe_schema(self):
        for v in [None,[],{'domains':'SECRET'}]:self.assertFalse(protocol_summary(v)['domains_array'])
    def probe(self,d):
        calls=[]
        def device(*args,**kwargs):calls.append(args);return SimpleNamespace(stdout=b'12345')
        return SimpleNamespace(args=SimpleNamespace(scope='note-search',serial='emulator-5562',tools_commit='a'*40,commit='b'*40),output=Path(d),shell=lambda *args:SimpleNamespace(stdout=b'4321'),device=device),calls
    def observe(self,probe,status,body):
        pages=json.dumps([{'type':'page','title':'OpenNexus','url':'http://tauri.localhost/'}]).encode()
        responses=iter([SimpleNamespace(status=200,read=lambda n:pages),SimpleNamespace(status=status,read=lambda n:body[:n])])
        calls=[]
        connection=SimpleNamespace(request=lambda *a:calls.append(a),getresponse=lambda:next(responses),close=lambda:None)
        with patch('android_protocol_observation.require_hosted_emulator'),patch('android_protocol_observation.http.client.HTTPConnection',return_value=connection) as connect,patch.dict(os.environ,GITHUB_RUN_ID='123'):
            value=observe_owned_protocol(probe)
        self.assertEqual(connect.call_count,2)
        self.assertEqual(calls,[('GET','/json/list'),('GET','/json/protocol')]);return value
    def test_actual_404_is_recorded_without_fabricated_domain_or_CDP_fallback(self):
        with tempfile.TemporaryDirectory() as d:
            p,calls=self.probe(d);r=self.observe(p,404,b'SECRET_HTTP_ERROR')
            self.assertEqual(r['protocol_HTTP_status'],404);self.assertNotIn('protocol_summary',r)
            self.assertFalse(r['Debugger_enabled']);self.assertFalse(r['CDP_command_or_native_IPC_sent']);self.assertEqual(calls[-1],('forward','--remove','tcp:12345'))
            self.assertNotIn('SECRET_HTTP_ERROR',(Path(d)/'owned-protocol-observation.json').read_text('utf-8'))
    def test_success_records_summary_without_raw_protocol_or_app_state(self):
        with tempfile.TemporaryDirectory() as d:
            p,_=self.probe(d);r=self.observe(p,200,json.dumps(self.protocol()).encode());self.assertEqual(r['protocol_summary']['getScriptSource_commands'],1);self.assertTrue(r['same_original_pid_after_HTTP'])
    def test_cleanup_failure_preserves_bounded_observation_without_replaying(self):
        with tempfile.TemporaryDirectory() as d:
            p,calls=self.probe(d);original=p.device
            def device(*args,**kwargs):
                if '--remove' in args:raise OSError('SECRET_DEVICE_ERROR')
                return original(*args,**kwargs)
            p.device=device;r=self.observe(p,404,b'')
            self.assertEqual(r['owned_forward_cleanup_error_class'],'OSError')
            self.assertTrue((Path(d)/'owned-protocol-observation.json').is_file())
            self.assertNotIn('SECRET',json.dumps(r));self.assertEqual(len(calls),1)
    def test_original_observation_is_exclusive_and_not_repeated(self):
        with tempfile.TemporaryDirectory() as d:
            p,calls=self.probe(d);self.observe(p,200,json.dumps(self.protocol()).encode());before=len(calls)
            with self.assertRaises(FileExistsError):self.observe(p,200,b'{}')
            self.assertEqual(len(calls),before)
    def test_local_or_phone_refuses_before_intent_or_forward(self):
        with tempfile.TemporaryDirectory() as d,patch.dict(os.environ,{},clear=True):
            p,calls=self.probe(d)
            with self.assertRaises(RuntimeError):observe_owned_protocol(p)
            self.assertFalse(calls);self.assertFalse(list(Path(d).iterdir()))

if __name__=='__main__':unittest.main()
