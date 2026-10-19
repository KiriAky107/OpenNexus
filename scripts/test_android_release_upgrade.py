import copy
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import subprocess
import hashlib

from android_release_upgrade import ReleaseUpgrade, FILES, original_fixture, installed_identity
from android_runtime_probe import BODY, DRAFT


class ReleaseUpgradeBoundaries(unittest.TestCase):
    def test_installed_original_code_name_and_uid_are_exact(self):
        expected=dict(version='0.1.0-alpha1',version_code=2)
        raw='    versionCode=2 minSdk=24 targetSdk=36\n    versionName=0.1.0-alpha1\n'
        listing='package:cc.kronecker.opennexus uid:10123\n'
        self.assertEqual(installed_identity(raw,listing,expected)['uid'],10123)
        for text,ids in [(raw.replace('Code=2','Code=1'),listing),(raw,listing.replace('opennexus ','opennexus.dev ')),(raw+raw,listing),(raw,listing.replace('10123','0'))]:
            with self.assertRaises(RuntimeError):installed_identity(text,ids,expected)

    def test_original_gui_saves_and_unconfirmed_draft_not_fabricated_as_upgrade(self):
        rows={name:dict(drafts=[],saves=[dict(operation_id=str(index),state='committed',error=None)]) for index,name in enumerate(FILES)}
        rows[FILES[0]]['drafts']=[dict(content=DRAFT,generation=2,saved_generation=1)]
        value=dict(owned_files=rows,raw_sha256={'vault/'+FILES[0]:hashlib.sha256(BODY.encode()).hexdigest()})
        self.assertEqual(original_fixture(value),['0','1'])
        for change in ('confirmed','duplicate','wrong-bytes','failed-save'):
            bad=copy.deepcopy(value)
            if change=='confirmed':bad['owned_files'][FILES[0]]['drafts'][0]['saved_generation']=2
            if change=='duplicate':bad['owned_files'][FILES[1]]['saves'][0]['operation_id']='0'
            if change=='wrong-bytes':bad['raw_sha256']['vault/'+FILES[0]]='a'*64
            if change=='failed-save':bad['owned_files'][FILES[1]]['saves'][0]['state']='failed'
            with self.subTest(change=change),self.assertRaises(RuntimeError):original_fixture(bad)

    def test_local_or_physical_device_is_rejected_before_output_or_subprocess(self):
        with tempfile.TemporaryDirectory() as folder,patch.dict('os.environ',{'GITHUB_ACTIONS':'false'}),patch('android_release_upgrade.bind_pair') as bind,patch('subprocess.run') as run:
            output=Path(folder)/'new'
            for serial in ('emulator-5562','phone-serial'):
                with self.assertRaises(RuntimeError):ReleaseUpgrade(Path(folder),Path(folder),Path(folder),output,serial)
            self.assertFalse(output.exists());bind.assert_not_called();run.assert_not_called()

    def test_unknown_install_preserves_intent_and_partial_output_without_replay(self):
        with tempfile.TemporaryDirectory() as folder:
            subject=object.__new__(ReleaseUpgrade);subject.output=Path(folder)
            subject.device=lambda *a,**kw:(_ for _ in ()).throw(subprocess.TimeoutExpired('adb',120,output=b'original partial',stderr=b'unknown'))
            with self.assertRaises(subprocess.TimeoutExpired):subject.original_command('update-successor',('install','-r','synthetic.apk'))
            self.assertEqual((Path(folder)/'update-successor.stdout').read_bytes(),b'original partial')
            with self.assertRaises(FileExistsError):subject.original_command('update-successor',('install','-r','synthetic.apk'))


if __name__=='__main__':unittest.main()
