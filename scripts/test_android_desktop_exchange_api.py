"""Synthetic API responses test reader boundaries, not actual product pairing."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from android_desktop_exchange import artifact_name, exchange_bytes
from android_desktop_exchange_api import OriginalRunAPI, signed_archive_target, wait_original_exchange
from android_desktop_handoff import HandoffError, canonical, encode
import test_android_desktop_exchange as fixtures


class ExchangeReader(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.Exchange();self.fixture.setUp();self.context=self.fixture.context
        self.env={'GITHUB_ACTIONS':'true','RUNNER_ENVIRONMENT':'github-hosted','RUNNER_OS':'Linux',
            'GITHUB_REPOSITORY':'KiriAky107/OpenNexus','GITHUB_RUN_ID':'1234','GITHUB_SHA':'a'*40,'GH_TOKEN':'PRIVATE_TEST_TOKEN_123456789'}

    def test_foreign_host_context_and_archive_destinations_refuse_before_network(self):
        for change in ({'GITHUB_SHA':'b'*40},{'RUNNER_ENVIRONMENT':'self-hosted'},{'GITHUB_RUN_ID':'1235'}):
            with self.subTest(change=change),patch('http.client.HTTPSConnection') as connect:
                with self.assertRaises(HandoffError):OriginalRunAPI(self.context,dict(self.env,**change))
                connect.assert_not_called()
        for url in ('http://owned.blob.core.windows.net/file','https://api.github.com/file',
                    'https://evil.example/file','https://user:password@owned.blob.core.windows.net/file',
                    'https://owned.blob.core.windows.net:8443/file'):
            with self.subTest(url=url),self.assertRaises(HandoffError):signed_archive_target(url)

    def test_original_archive_download_never_forwards_the_API_token_and_partial_is_not_retried(self):
        with tempfile.TemporaryDirectory() as work:
            api=OriginalRunAPI(self.context,self.env)
            api.request=Mock(return_value='https://owned.blob.core.windows.net/original?sig=owned')
            connection=Mock();response=connection.getresponse.return_value;response.status=200
            response.read.side_effect=[b'original',b''];item={'id':99,'digest':'sha256:'+hashlib.sha256(b'original').hexdigest()}
            with patch('http.client.HTTPSConnection',return_value=connection):api.archive(item,Path(work)/'original.zip')
            self.assertNotIn('Authorization',connection.request.call_args.kwargs['headers'])
            with patch('http.client.HTTPSConnection') as connect:
                with self.assertRaises(HandoffError):api.archive(item,Path(work)/'original.zip')
                connect.assert_not_called()
        with tempfile.TemporaryDirectory() as work:
            api=OriginalRunAPI(self.context,self.env);api.request=Mock(return_value='https://owned.blob.core.windows.net/original')
            connection=Mock();response=connection.getresponse.return_value;response.status=200;response.read.side_effect=[b'partial',TimeoutError()]
            with patch('http.client.HTTPSConnection',return_value=connection):
                with self.assertRaisesRegex(RuntimeError,'PAIR_API_ORIGINAL_ARCHIVE_UNCONFIRMED'):api.archive(item,Path(work)/'original.zip')
            self.assertTrue((Path(work)/'original.partial').exists())
            with self.assertRaises(HandoffError):api.archive(item,Path(work)/'original.zip')
            self.assertEqual(connection.request.call_count,1)

    def test_only_verified_original_upload_downloads_once_and_cached_original_is_read_without_polling(self):
        metadata,raw=self.fixture.archive('recipient',exchange_bytes(self.context,'recipient',{'public_key':encode(self.fixture.public)}))
        run=dict(self.fixture.run,status='in_progress')
        api=Mock();api.request.side_effect=[run,{'total_count':1,'artifacts':[metadata]},{'total_count':2,'jobs':self.fixture.jobs}]
        api.archive.side_effect=lambda item,path:path.write_bytes(raw)
        with tempfile.TemporaryDirectory() as work:
            cache=Path(work)/'recipient'
            body,provenance=wait_original_exchange(self.context,'recipient',api,cache,timeout=1)
            self.assertEqual(body,{'public_key':encode(self.fixture.public)});self.assertEqual(provenance['original_job_id'],61)
            api.archive.assert_called_once();self.assertEqual(api.request.call_count,3)
            again=wait_original_exchange(self.context,'recipient',api,cache,timeout=1)
            self.assertEqual(again,(body,provenance));self.assertEqual(api.request.call_count,3)
            self.assertNotIn(self.env['GH_TOKEN'],''.join(x.read_text('utf-8') for x in cache.glob('*.json')))
        bad=copy.deepcopy(metadata);bad['workflow_run']['id']=1235
        api=Mock();api.request.side_effect=[run,{'artifacts':[bad]},{'jobs':self.fixture.jobs}]
        with tempfile.TemporaryDirectory() as work:
            with self.assertRaises(HandoffError):wait_original_exchange(self.context,'recipient',api,Path(work)/'bad',timeout=1)
            api.archive.assert_not_called()

    def test_failed_original_producer_and_wrong_run_never_poll_or_download_an_alternative(self):
        run=dict(self.fixture.run,status='in_progress');jobs=copy.deepcopy(self.fixture.jobs);jobs[0]['conclusion']='failure'
        api=Mock();api.request.side_effect=[run,{'artifacts':[]},{'jobs':jobs}]
        with tempfile.TemporaryDirectory() as work,patch('android_desktop_exchange_api.time.sleep') as sleep:
            with self.assertRaises(HandoffError):wait_original_exchange(self.context,'recipient',api,Path(work)/'failed',timeout=1)
            sleep.assert_not_called();api.archive.assert_not_called()
        api=Mock();api.request.return_value=dict(run,id=1235)
        with tempfile.TemporaryDirectory() as work:
            with self.assertRaises(HandoffError):wait_original_exchange(self.context,'recipient',api,Path(work)/'wrong',timeout=1)
            api.request.assert_called_once();api.archive.assert_not_called()
        metadata,_=self.fixture.archive('recipient',exchange_bytes(self.context,'recipient',{'public_key':encode(self.fixture.public)}))
        jobs=copy.deepcopy(self.fixture.jobs);jobs[0]['steps'][0]['conclusion']='failure'
        api=Mock();api.request.side_effect=[run,{'artifacts':[metadata]},{'jobs':jobs}]
        with tempfile.TemporaryDirectory() as work,patch('android_desktop_exchange_api.time.sleep') as sleep:
            with self.assertRaisesRegex(HandoffError,'PAIR_EXCHANGE_UPLOAD_INCOMPLETE'):
                wait_original_exchange(self.context,'recipient',api,Path(work)/'failed-upload',timeout=1)
            sleep.assert_not_called();api.archive.assert_not_called()

    def test_frozen_metadata_without_body_resumes_only_original_ID_but_partial_still_refuses(self):
        metadata,raw=self.fixture.archive('recipient',exchange_bytes(self.context,'recipient',{'public_key':encode(self.fixture.public)}))
        for partial in (False,True):
            with self.subTest(partial=partial),tempfile.TemporaryDirectory() as work:
                cache=Path(work)/'recipient';cache.mkdir()
                (cache/'intent.json').write_bytes(canonical({'context':self.context,'stage':'recipient','artifact_name':artifact_name(self.context,'recipient')}))
                for name,body in [('run.json',self.fixture.run),('jobs.json',self.fixture.jobs),('artifact.json',metadata)]:
                    (cache/name).write_bytes(canonical(body))
                if partial:(cache/'original.partial').write_bytes(b'original partial bytes')
                api=Mock();api.archive.side_effect=lambda item,path:path.write_bytes(raw)
                if partial:
                    with self.assertRaises(HandoffError):wait_original_exchange(self.context,'recipient',api,cache,timeout=1)
                    api.archive.assert_not_called()
                else:
                    body,_=wait_original_exchange(self.context,'recipient',api,cache,timeout=1)
                    self.assertEqual(body,{'public_key':encode(self.fixture.public)})
                    api.archive.assert_called_once_with(metadata,cache/'original.zip')
                api.request.assert_not_called()

    def test_visible_original_waits_for_its_named_upload_step_without_an_early_archive_download(self):
        metadata,raw=self.fixture.archive('recipient',exchange_bytes(self.context,'recipient',{'public_key':encode(self.fixture.public)}))
        run=dict(self.fixture.run,status='in_progress');pending=copy.deepcopy(self.fixture.jobs)
        pending[0]['steps'][0].update(status='in_progress',conclusion=None,completed_at=None)
        api=Mock();api.request.side_effect=[run,{'artifacts':[metadata]},{'jobs':pending},run,{'artifacts':[metadata]},{'jobs':self.fixture.jobs}]
        api.archive.side_effect=lambda item,path:path.write_bytes(raw)
        with tempfile.TemporaryDirectory() as work,patch('android_desktop_exchange_api.time.sleep') as sleep:
            body,_=wait_original_exchange(self.context,'recipient',api,Path(work)/'original',timeout=1)
            self.assertEqual(body,{'public_key':encode(self.fixture.public)})
            api.archive.assert_called_once();self.assertEqual(api.request.call_count,6);sleep.assert_called_once()

    def test_pending_original_artifact_identity_is_frozen_before_waiting_and_cannot_be_replaced(self):
        metadata,_=self.fixture.archive('recipient',exchange_bytes(self.context,'recipient',{'public_key':encode(self.fixture.public)}))
        run=dict(self.fixture.run,status='in_progress');pending=copy.deepcopy(self.fixture.jobs)
        pending[0]['steps'][0].update(status='in_progress',conclusion=None,completed_at=None)
        replacement=dict(metadata,id=metadata['id']+1)
        api=Mock();api.request.side_effect=[run,{'artifacts':[metadata]},{'jobs':pending},run,{'artifacts':[replacement]},{'jobs':self.fixture.jobs}]
        with tempfile.TemporaryDirectory() as work,patch('android_desktop_exchange_api.time.sleep'):
            cache=Path(work)/'original'
            with self.assertRaisesRegex(HandoffError,'PAIR_API_ORIGINAL_ARTIFACT_CHANGED'):
                wait_original_exchange(self.context,'recipient',api,cache,timeout=1)
            api.archive.assert_not_called()
            self.assertEqual(json.loads((cache/'observed-artifact.json').read_bytes())['id'],metadata['id'])


if __name__=='__main__':unittest.main(verbosity=2)
