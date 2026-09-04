"""One offline inference process. Heavy libraries stay out of the API process."""
from __future__ import annotations

import contextlib
import json
import os
import sys
import threading
import time


def decode(path, *, limit_seconds=3600):
    import av
    import numpy as np
    frames = []
    samples = 0
    with av.open(path, options={"protocol_whitelist": "file,pipe"}) as container:
        if not container.streams.audio:
            raise ValueError("Media has no audio track")
        resampler = av.AudioResampler(format="fltp", layout="mono", rate=16000)
        for frame in container.decode(audio=0):
            for output in resampler.resample(frame):
                audio = output.to_ndarray().reshape(-1)
                samples += len(audio)
                if samples > limit_seconds * 16000:
                    raise ValueError("Audio exceeds one hour")
                frames.append(audio)
        for output in resampler.resample(None):
            frames.append(output.to_ndarray().reshape(-1))
    if not frames:
        raise ValueError("Audio is empty")
    audio = np.concatenate(frames).astype(np.float32)
    if not np.isfinite(audio).all() or len(audio) < 1600:
        raise ValueError("Invalid or too short audio")
    return audio


def speech_regions(audio):
    """Energy-based segmentation, not word alignment; retain original sample offsets."""
    import numpy as np
    window = 480
    energies = [float(np.sqrt(np.mean(audio[i:i + window] ** 2))) for i in range(0, len(audio), window)]
    threshold = max(0.002, float(np.percentile(energies, 20)) * 2)
    active = [i for i, energy in enumerate(energies) if energy >= threshold]
    if not active:
        return []
    regions, start, previous = [], active[0], active[0]
    for index in active[1:]:
        if index - previous > 20 or (index - start) * window >= 20 * 16000:
            regions.append((max(0, start * window - 2400), min(len(audio), (previous + 1) * window + 2400)))
            start = index
        previous = index
    regions.append((max(0, start * window - 2400), min(len(audio), (previous + 1) * window + 2400)))
    return regions


def speaker_model(path, device):
    import torch
    from modelscope.models.audio.sv.ERes2NetV2 import ERes2NetV2
    from pathlib import Path
    model = ERes2NetV2(baseWidth=26, scale=2, expansion=2, embed_dim=192)
    weights = torch.load(Path(path) / "pretrained_eres2netv2.ckpt", map_location="cpu", weights_only=True)
    model.load_state_dict(weights, strict=True)
    return model.to(device).eval()


def voice_embedding(model, audio, device):
    import torch
    import torchaudio.compliance.kaldi as kaldi
    if len(audio) < 16000:
        raise ValueError("Speaker comparison needs at least one second of audio")
    features = kaldi.fbank(torch.from_numpy(audio).unsqueeze(0), num_mel_bins=80, sample_frequency=16000)
    features -= features.mean(dim=0, keepdim=True)
    with torch.inference_mode():
        vector = model(features.unsqueeze(0).to(device)).flatten()
        return torch.nn.functional.normalize(vector, dim=0)


def run(request):
    import torch
    import psutil
    config, payload = request["config"], request["payload"]
    torch.set_num_threads(config["cpu_threads"])
    requested = config["device"]
    device = "cuda:0" if requested == "cuda" and torch.cuda.is_available() else "cpu"
    if device != "cpu":
        total = torch.cuda.get_device_properties(0).total_memory
        torch.cuda.set_per_process_memory_fraction(min(1.0, config["gpu_memory_limit_mb"] * 1024 ** 2 / total))
    process = psutil.Process()
    peak = [0]
    stop = threading.Event()

    def monitor():
        while not stop.wait(0.2):
            used = process.memory_info().rss
            peak[0] = max(peak[0], used)
            if used > config["memory_limit_mb"] * 1024 ** 2:
                os._exit(75)

    threading.Thread(target=monitor, daemon=True).start()
    started = time.monotonic()
    path, operation = request["model_path"], request["operation"]
    try:
        usage = {}
        if operation == "embedding":
            from sentence_transformers import SentenceTransformer
            model = SentenceTransformer(path, device=device, local_files_only=True, trust_remote_code=False,
                                        model_kwargs={"attn_implementation": "sdpa"})
            loaded = time.monotonic()
            result = model.encode(payload["texts"], batch_size=4, normalize_embeddings=True, show_progress_bar=False).tolist()
            # Count the tokenizer's actual encoded input, not characters or words.
            usage = {"input_tokens": int(model.tokenize(payload["texts"])["attention_mask"].sum())}
        elif operation == "transcription":
            from qwen_asr import Qwen3ASRModel
            model = Qwen3ASRModel.from_pretrained(path, dtype=torch.float32 if device == "cpu" else torch.float16,
                device_map=device, attn_implementation="sdpa", max_inference_batch_size=1, max_new_tokens=512)
            loaded = time.monotonic()
            audio = decode(payload["source"])
            regions = speech_regions(audio)
            language = {"zh": "Chinese", "en": "English", "ja": "Japanese", "yue": "Cantonese"}.get(payload.get("language"), payload.get("language"))
            segments = []
            for start, end in regions:
                output = model.transcribe(audio=(audio[start:end], 16000), language=language)[0]
                if output.text.strip():
                    segments.append({"segment_id": f"segment_{len(segments) + 1}", "start_time": start / 16000,
                                     "end_time": end / 16000, "text": output.text, "language": output.language})
                    sys.__stdout__.write(json.dumps({"progress": end / len(audio), "segment": segments[-1]}, ensure_ascii=False) + "\n")
                    sys.__stdout__.flush()
            result = {"text": "\n".join(s["text"] for s in segments), "segments": segments}
        elif operation == "speaker_matching":
            model = speaker_model(path, device)
            loaded = time.monotonic()
            first = voice_embedding(model, decode(payload["source"]), device)
            second = voice_embedding(model, decode(payload["reference"]), device)
            # Similarity, not a calibrated identity probability.
            result = {"score": max(0.0, min(1.0, float(torch.dot(first, second))))}
        elif operation == "diarization":
            model = speaker_model(path, device)
            loaded = time.monotonic()
            audio = decode(payload["source"])
            centroids, speakers = [], []
            for segment in payload["segments"]:
                sample = audio[int(segment["start_time"] * 16000):int(segment["end_time"] * 16000)]
                if len(sample) < 16000:
                    speakers.append(None)
                    continue
                vector = voice_embedding(model, sample, device)
                similarities = [float(torch.dot(vector, c)) for c in centroids]
                best = max(range(len(similarities)), key=similarities.__getitem__) if similarities else None
                if best is None or similarities[best] < 0.36:
                    best = len(centroids)
                    centroids.append(vector)
                speakers.append(f"speaker_{best + 1}")
            result = {"speakers": speakers}
        else:
            raise ValueError("Unknown inference operation")
        return {"result": result, "usage": usage, "diagnostics": {"requested_device": requested, "actual_device": device,
                "fallback_reason": "CUDA_UNAVAILABLE" if requested == "cuda" and device == "cpu" else None,
                "load_seconds": loaded - started, "inference_seconds": time.monotonic() - loaded,
                "peak_memory_bytes": max(peak[0], process.memory_info().rss), "operation": operation}}
    finally:
        stop.set()


if __name__ == "__main__":
    request = json.loads(sys.stdin.buffer.read())
    # Third-party progress/logging must never corrupt the protocol or leak into API errors.
    with contextlib.redirect_stdout(sys.stderr):
        try:
            response = run(request)
        except (ImportError, ModuleNotFoundError):
            response = {"error_code": "LOCAL_RUNTIME_DEPENDENCY_MISSING", "message": "本地模型运行依赖不完整，请重新运行安装脚本。"}
        except Exception:
            response = {"error_code": "LOCAL_INFERENCE_FAILED", "message": "本地推理失败，请检查媒体格式、模型和设备配置。"}
    sys.stdout.buffer.write((json.dumps(response, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8"))
