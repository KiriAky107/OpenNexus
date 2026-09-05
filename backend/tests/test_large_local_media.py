import asyncio
import sys
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from app.errors import ApiError
from app.providers.routing import ModelRoutingService, MAX_LOCAL_MEDIA_BYTES, MAX_MEDIA_BYTES, RoutedTranscript
from app.services import transcription_service as jobs
from app.config import get_settings


def test_large_media_requires_local_only_and_respects_size_limit():
    path = get_settings().attachments_path / 'large.mp3'
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('wb') as file:
        file.truncate(MAX_MEDIA_BYTES + 1)
    with pytest.raises(ApiError):
        ModelRoutingService._media_file(path)
    with ModelRoutingService._media_file(path, local_only=True):
        pass
    with pytest.raises(ApiError):
        asyncio.run(jobs.create_transcription('large.mp3', local_only=False))
    with path.open('wb') as file:
        file.truncate(MAX_LOCAL_MEDIA_BYTES + 1)
    with pytest.raises(ApiError):
        ModelRoutingService._media_file(path, local_only=True)


def test_decode_recovers_one_corrupt_packet_without_shifting_following_audio(monkeypatch):
    from app.local_models.worker import decode
    class Samples(list):
        def reshape(self, *_): return self
        def astype(self, *_): return self
        def to_ndarray(self): return self
    class InvalidDataError(Exception): pass
    def broken(): raise InvalidDataError()
    packets = [SimpleNamespace(decode=lambda: [Samples([1] * 3200)]),
               SimpleNamespace(decode=broken, duration=100, time_base=.001),
               SimpleNamespace(decode=lambda: [Samples([2] * 3200)])]
    container = SimpleNamespace(streams=SimpleNamespace(audio=[1]), demux=lambda **_: iter(packets))
    fake_av = SimpleNamespace(open=lambda *_a, **_kw: nullcontext(container),
        error=SimpleNamespace(InvalidDataError=InvalidDataError),
        AudioResampler=lambda **_: SimpleNamespace(resample=lambda frame: [] if frame is None else [frame]))
    fake_numpy = SimpleNamespace(float32=float, zeros=lambda count, **_: Samples([0] * count),
        concatenate=lambda frames: Samples(value for frame in frames for value in frame),
        isfinite=lambda _: SimpleNamespace(all=lambda: True))
    monkeypatch.setitem(sys.modules, 'av', fake_av)
    monkeypatch.setitem(sys.modules, 'numpy', fake_numpy)
    warnings = []
    output = decode('test.mp3', warnings=warnings)
    assert output == [1] * 3200 + [0] * 1600 + [2] * 3200
    assert warnings == ['MEDIA_CORRUPT_PACKETS_SKIPPED:1']
    with pytest.raises(ValueError, match='one hour'):
        decode('test.mp3', limit_seconds=.25)


def test_decode_warning_reaches_persisted_job(monkeypatch):
    from app.container import container
    path = get_settings().attachments_path / 'audio.mp3'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'audio')
    async def transcribe(*_args, **_kwargs):
        return RoutedTranscript(text='decoded', source='local', warnings=['MEDIA_CORRUPT_PACKETS_SKIPPED:1'])
    monkeypatch.setattr(container.model_routing, 'transcribe', transcribe)
    job = asyncio.run(jobs.create_transcription('audio.mp3', local_only=True))
    assert job.status == 'completed'
    assert jobs.require_job(job.job_id).warnings == ['MEDIA_CORRUPT_PACKETS_SKIPPED:1']
