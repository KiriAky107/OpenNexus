"""Canvas-only contracts and workflow admission, not an executed ENOSPC claim."""
from copy import deepcopy
from pathlib import Path
import hashlib,json,unittest
from unittest.mock import Mock,patch
import yaml
import android_storage_probe as subject
import test_android_storage_probe as storage_fixtures
from android_workflow_gate import admitted

ORIGINAL=b'{"nodes":[],"edges":[]}\n'
def updated():return json.dumps(dict(nodes=[dict(id='117e805a-e3a3-4279-9d0c-14546de90d74',type='text',x=95,y=190,width=300,height=180,text=subject.CANVAS_TEXT)],edges=[]),ensure_ascii=False).encode()

class CanvasStorageBoundaries(unittest.TestCase):
    def test_reviewed_draft_is_the_same_single_original_text_node_before_filling(self):
        self.assertEqual(subject.validate_canvas_draft(ORIGINAL,updated()),'117e805a-e3a3-4279-9d0c-14546de90d74')
        for change in ('foreign_root','text','node','invalid_id'):
            wrong=json.loads(updated())
            if change=='foreign_root':wrong['foreign']='unreviewed'
            elif change=='text':wrong['nodes'][0]['text']='other'
            elif change=='node':wrong['nodes'].append(deepcopy(wrong['nodes'][0]))
            else:wrong['nodes'][0]['id']='not-an-original-uuid'
            with self.subTest(change=change),self.assertRaises((RuntimeError,ValueError)):
                subject.validate_canvas_draft(ORIGINAL,json.dumps(wrong).encode())

    def test_exact_reviewed_canvas_bytes_bind_pending_native_cas_and_spool_hash(self):
        before,failed=storage_fixtures.StorageBoundaries().pending_fixture();raw=updated();digest=hashlib.sha256(raw).hexdigest()
        before['files'][0]['hash']=hashlib.sha256(ORIGINAL).hexdigest()
        failed['files']=deepcopy(before['files']);failed['saves'][1].update(content=raw.decode(),expected=hashlib.sha256(ORIGINAL).hexdigest())
        failed['drafts'][0]['content']=raw.decode();failed['payloads'][0].update(hash=digest,size=len(raw))
        self.assertEqual(subject.original_pending(before,failed,ORIGINAL,raw),failed['saves'][1])
        with self.assertRaises(RuntimeError):subject.original_pending(before,failed)
        wrong=deepcopy(failed);wrong['payloads'][0]['hash']='0'*64
        with self.assertRaises(RuntimeError):subject.original_pending(before,wrong,ORIGINAL,raw)

    def test_physical_or_local_canvas_scope_cannot_reach_root_or_mount(self):
        probe=Mock();probe.args.scope='canvas-storage-recovery';probe.args.serial='physical-phone'
        with patch.dict(subject.os.environ,{},clear=True),self.assertRaises(RuntimeError):subject.OwnedFilesystem(probe)
        probe.shell.assert_not_called();probe.device.assert_not_called()

    def test_workflow_admits_only_reused_runtime_and_no_new_apk_or_desktop(self):
        root=Path(__file__).resolve().parents[1];workflow=yaml.load((root/'.github/workflows/android.yml').read_text('utf-8'),Loader=yaml.BaseLoader)
        scope='canvas-storage-recovery'
        for job in ('debug-apk','desktop-startup','desktop-pair','android-desktop-pair'):
            self.assertFalse(admitted(workflow['jobs'][job]['if'],scope,reuse='39148381613',build='skipped'))
        self.assertTrue(admitted(workflow['jobs']['emulator-runtime']['if'],scope,reuse='39148381613',build='skipped'))
        steps=workflow['jobs']['emulator-runtime']['steps']
        self.assertTrue(any('test_android_canvas_storage_probe.py' in s.get('run','') and scope in s.get('if','') for s in steps))
        for script in ('scripts/android_auth_runtime.py','scripts/android_sync_runtime.py'):
            step=next(s for s in steps if script in s.get('run',''))
            self.assertFalse(admitted(step['if'],scope,reuse='39148381613',build='skipped'))

    def test_canvas_driver_has_one_new_save_and_one_explicit_original_retry(self):
        root=Path(__file__).resolve().parents[1];source=(root/'scripts/android-runtime/RuntimeProbe.java').read_text('utf-8')
        method=source.split('private void canvasStorageRecovery(String phase)',1)[1].split('private void storageRecovery',1)[0]
        self.assertEqual(method.count('click("Save",false)'),1)
        self.assertEqual(method.count('click("Retry original content",false)'),1)
        self.assertEqual(method.count('click("Check original result",false)'),1)
        self.assertIn(subject.CANVAS_NOTE,method);self.assertIn(subject.CANVAS_TEXT,method)
        self.assertNotIn('inputText(noteBody()',method)
        self.assertIn('waitLabel("FILESYSTEM_ERROR")',method)

if __name__=='__main__':unittest.main()
