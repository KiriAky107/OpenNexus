"""The new image phase cannot fall back to old reads or continue after unknown capture."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch

import android_release_sync_image as runtime


class SyncImageSceneChecks(unittest.TestCase):
    def test_unknown_image_original_has_no_read_fallback_or_synthetic_pixel_success(self):
        subject=object.__new__(runtime.ReleaseSyncImage)
        with patch.object(runtime,'run_image',side_effect=TimeoutError('unknown original crop')) as original, \
            patch.object(runtime.ReleaseSyncRead,'phase') as old:
            with self.assertRaises(TimeoutError):subject.phase(runtime.PHASE,'canvas-image')
            original.assert_called_once_with(subject,'canvas-image');old.assert_not_called()
            for phase in ('release-sync-read-canvas','release-sync-read-markdown','arbitrary'):
                with self.assertRaises(RuntimeError):subject.phase(phase,'no-fallback')
            old.assert_not_called()

    def test_unknown_new_association_stops_before_peer_capture_or_any_old_Save_replay(self):
        subject=object.__new__(runtime.ReleaseSyncImage)
        with tempfile.TemporaryDirectory() as folder:
            subject.output=Path(folder);subject.owner=Path(folder);subject.prepare=Mock();subject.stage_sync_input=Mock()
            calls=[]
            def phase(value,label):
                calls.append(value)
                if value=='associate':raise TimeoutError('unknown new Confirm')
            subject.phase=phase;subject.snapshot=Mock(return_value={'intents':[{'device_id':'owned'}]})
            subject.download_snapshot=Mock(return_value=({'native':{'binding':None},'all_clean_drafts':[]},{}))
            with patch.object(runtime,'active_intent'),patch.object(runtime,'Setup') as setup,patch.object(runtime,'ReleasePeer') as peer,patch.object(runtime,'run_image') as image:
                setup.return_value.run.return_value={'vault':{},'provider':{'rows':{'revisions':[]}}}
                with self.assertRaises(TimeoutError):subject.run()
                self.assertEqual(calls,['login-a','associate']);peer.assert_not_called();image.assert_not_called()
                self.assertFalse((Path(folder)/'sync-image-runtime-proof.json').exists())


if __name__=='__main__':unittest.main()
