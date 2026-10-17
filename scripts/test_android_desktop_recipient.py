"""Owned-key source boundaries; no real Windows app or paired job is started."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import android_desktop_recipient as recipient
from android_desktop_handoff import HandoffError, canonical, decode, seal
from android_desktop_product import DESKTOP_SOURCE, directory_identity


class ReceivingKey(unittest.TestCase):
    def setUp(self):
        self.context={'run_id':1234,'tools_commit':'a'*40,'android_commit':'b'*40,'desktop_commit':DESKTOP_SOURCE}

    def owned_claim(self,root):
        def claim(env,context):
            root.mkdir()
            (root/'owner.json').write_bytes(canonical({'context':context,'root_identity':directory_identity(root)}))
            return root
        return claim

    def test_real_host_guard_precedes_key_generation_or_user_files(self):
        with patch.dict('os.environ',{},clear=True),patch.object(recipient,'new_recipient') as generate:
            with self.assertRaises(HandoffError):recipient.prepare_receiving_key(self.context)
            generate.assert_not_called()

    def test_only_public_key_is_exchanged_and_original_verified_ciphertext_decrypts_in_private_memory(self):
        with tempfile.TemporaryDirectory() as work:
            root=Path(work)/'owned'
            with patch.object(recipient,'claim_root',side_effect=self.owned_claim(root)):
                path,proof=recipient.prepare_receiving_key(self.context)
            body=json.loads(path.read_bytes());public=decode(body['body']['public_key'],32)
            self.assertEqual(set(body['body']),{'public_key'})
            self.assertNotIn((root/'receiving-key.bin').read_bytes().hex(),path.read_text('utf-8'))
            payload={'endpoint':'https://owned-test.trycloudflare.com/','username':'android-owned-a-'+'d'*12,
                     'password':'P'*48,'vault_id':'e'*32,'vault_name':'Owned Android Sync '+'e'*32}
            sealed=seal(public,payload,self.context)
            api=object();cache=root/'sealed-cache';provenance={'original_run_id':1234,'original_job_id':62}
            with patch.object(recipient,'owned_root',return_value=root),patch.object(recipient,'wait_original_exchange',return_value=(sealed,provenance)) as wait:
                actual,actual_origin=recipient.receive_original_fixture(self.context,api,cache)
                self.assertEqual((actual,actual_origin),(payload,provenance))
                wait.assert_called_once_with(self.context,'sealed',api,cache)
            for file in root.rglob('*.json'):
                self.assertNotIn(payload['password'],file.read_text('utf-8'))
            self.assertFalse(proof['APK_or_release_signing'])

    def test_changed_private_key_or_other_run_is_refused_before_archive_read(self):
        for change in ('key','context'):
            with self.subTest(change=change),tempfile.TemporaryDirectory() as work:
                root=Path(work)/'owned'
                with patch.object(recipient,'claim_root',side_effect=self.owned_claim(root)):
                    recipient.prepare_receiving_key(self.context)
                context=dict(self.context)
                if change=='key':(root/'receiving-key.bin').write_bytes(b'X'*32)
                else:context['run_id']=1235
                with patch.object(recipient,'owned_root',return_value=root),patch.object(recipient,'wait_original_exchange') as wait:
                    with self.assertRaises(HandoffError):recipient.receive_original_fixture(context,object(),root/'sealed-cache')
                    wait.assert_not_called()


if __name__=='__main__':unittest.main(verbosity=2)
