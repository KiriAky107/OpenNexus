"""单个离线推理进程；重量级依赖不会加载到 API 进程中。"""
from __future__ import annotations

import contextlib
import json
import os
import sys
import threading
import time

# Worker 在发布包的临时挂载目录中运行，不能留下会触发 Core 完整性校验的字节码。
sys.dont_write_bytecode = True

# 桌面 Host 只向 Core 传入最小环境。PyTorch 编译缓存会通过 getpass
# 读取用户名；在 Windows 上缺少 USERNAME 时，它会误尝试导入 Unix 的 pwd。
os.environ.setdefault(
    "USERNAME", os.path.basename(os.environ.get("USERPROFILE", "OpenNexus"))
)
os.environ.setdefault(
    "TORCHINDUCTOR_CACHE_DIR",
    os.path.join(
        os.environ.get("LOCALAPPDATA", os.environ.get("TEMP", ".")),
        "OpenNexus",
        "torchinductor",
    ),
)


def decode(path, *, limit_seconds=3600, warnings=None):
    import av
    import numpy as np
    frames = []
    samples = 0
    corrupt = 0
    with av.open(path, options={"protocol_whitelist": "file,pipe"}) as container:
        if not container.streams.audio:
            raise ValueError("Media has no audio track")
        resampler = av.AudioResampler(format="fltp", layout="mono", rate=16000)
        for packet in container.demux(audio=0):
            try:
                decoded = packet.decode()
            except av.error.InvalidDataError:
                corrupt += 1
                if corrupt > 100:
                    raise ValueError("Too many damaged audio packets")
                # 将丢失数据包的持续时间保留为静音，以便后面的时间戳不会发生变化。
                missing = max(0, round(float((packet.duration or 0) * (packet.time_base or 0)) * 16000))
                samples += missing
                if samples > limit_seconds * 16000:
                    raise ValueError("Audio exceeds one hour")
                if missing:
                    frames.append(np.zeros(missing, dtype=np.float32))
                continue
            for frame in decoded:
                for output in resampler.resample(frame):
                    audio = output.to_ndarray().reshape(-1)
                    samples += len(audio)
                    if samples > limit_seconds * 16000:
                        raise ValueError("Audio exceeds one hour")
                    frames.append(audio)
        for output in resampler.resample(None):
            audio = output.to_ndarray().reshape(-1)
            samples += len(audio)
            if samples > limit_seconds * 16000:
                raise ValueError("Audio exceeds one hour")
            frames.append(audio)
    if not frames:
        raise ValueError("Audio is empty")
    audio = np.concatenate(frames).astype(np.float32)
    if corrupt and warnings is not None:
        warnings.append(f"MEDIA_CORRUPT_PACKETS_SKIPPED:{corrupt}")
    if not np.isfinite(audio).all() or len(audio) < 1600:
        raise ValueError("Invalid or too short audio")
    return audio


def speech_regions(audio):
    """基于能量的切分，而不是词对齐；保留原始样本偏移量。"""
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


def _normalized_vector(values):
    """把声纹向量转成普通列表并归一化，便于在无 PyTorch 的 API 测试环境中验证聚类。"""
    import math
    values = [float(value) for value in values]
    norm = math.sqrt(sum(value * value for value in values))
    if not values or not math.isfinite(norm) or norm <= 1e-12:
        raise ValueError("Invalid speaker embedding")
    return [value / norm for value in values]


def _similarity(left, right):
    return sum(a * b for a, b in zip(left, right, strict=True))


def cluster_speaker_embeddings(embeddings, segments, *, threshold=0.36):
    """聚类片段声纹，并把过短片段交给相邻的稳定说话人。

    质心在每次接收新样本后更新，避免第一段永久决定整簇。持续时间不超过
    3 秒的孤立单例通常是停顿处的语气词；将它并入最相近的已有稳定簇，
    同时保留由多个片段支持的第三位及更多说话人。
    """
    if len(embeddings) != len(segments):
        raise ValueError("Speaker embeddings and segments must have the same length")
    vectors = [None if value is None else _normalized_vector(value) for value in embeddings]
    assignments = [None] * len(vectors)
    clusters = []
    for index, vector in enumerate(vectors):
        if vector is None:
            continue
        similarities = [_similarity(vector, cluster["centroid"]) for cluster in clusters]
        best = max(range(len(similarities)), key=similarities.__getitem__) if similarities else None
        if best is None or similarities[best] < threshold:
            best = len(clusters)
            clusters.append({"members": [], "sum": [0.0] * len(vector), "centroid": vector})
        cluster = clusters[best]
        cluster["members"].append(index)
        cluster["sum"] = [total + value for total, value in zip(cluster["sum"], vector, strict=True)]
        cluster["centroid"] = _normalized_vector(cluster["sum"])
        assignments[index] = best

    # 短语气词可能形成只有一个片段的离群簇。仅合并短单例，不吞掉由多个
    # 片段支持的真实少数说话人。
    stable = [index for index, cluster in enumerate(clusters) if len(cluster["members"]) > 1]
    for index, cluster in enumerate(clusters):
        member = cluster["members"][0] if len(cluster["members"]) == 1 else None
        if member is None or not stable:
            continue
        duration = float(segments[member]["end_time"]) - float(segments[member]["start_time"])
        if duration > 3.0:
            continue
        target = max(stable, key=lambda other: _similarity(cluster["centroid"], clusters[other]["centroid"]))
        assignments[member] = target

    # 没有足够语音生成声纹的短片段继承时间上最近的稳定标签。同一说话人
    # 两个片段之间的语气词会优先落回该说话人。
    labeled = [index for index, value in enumerate(assignments) if value is not None]
    for index, value in enumerate(assignments):
        if value is not None or not labeled:
            continue
        previous = next((item for item in reversed(labeled) if item < index), None)
        following = next((item for item in labeled if item > index), None)
        if previous is not None and following is not None and assignments[previous] == assignments[following]:
            assignments[index] = assignments[previous]
            continue
        candidates = []
        if previous is not None:
            distance = max(0.0, float(segments[index]["start_time"]) - float(segments[previous]["end_time"]))
            candidates.append((distance, 0, assignments[previous]))
        if following is not None:
            distance = max(0.0, float(segments[following]["start_time"]) - float(segments[index]["end_time"]))
            candidates.append((distance, 1, assignments[following]))
        assignments[index] = min(candidates)[2] if candidates else None

    # 合并后按首次出现顺序重新编号，避免 speaker_1、speaker_3 这样的空洞 ID。
    remap = {}
    speakers = []
    for value in assignments:
        if value is None:
            speakers.append(None)
            continue
        remap.setdefault(value, len(remap) + 1)
        speakers.append(f"speaker_{remap[value]}")
    return speakers


class CudaInitializationError(RuntimeError):
    pass


def run(request):
    import torch
    import psutil
    config, payload = request["config"], request["payload"]
    torch.set_num_threads(config["cpu_threads"])
    requested = config["device"]
    try:
        device = "cuda:0" if requested == "cuda" and torch.cuda.is_available() else "cpu"
        if device != "cpu":
            torch.cuda.init()
            total = torch.cuda.get_device_properties(0).total_memory
            torch.cuda.set_per_process_memory_fraction(min(1.0, config["gpu_memory_limit_mb"] * 1024 ** 2 / total))
    except Exception as exc:
        raise CudaInitializationError() from exc
    request["_actual_device"] = device
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
        audio_seconds = None
        if operation == "embedding":
            from sentence_transformers import SentenceTransformer
            model = SentenceTransformer(path, device=device, local_files_only=True, trust_remote_code=False,
                                        model_kwargs={"attn_implementation": "sdpa"})
            loaded = time.monotonic()
            result = model.encode(payload["texts"], batch_size=4, normalize_embeddings=True, show_progress_bar=False).tolist()
            # 计算分词器的实际编码输入，而不是字符或单词。
            usage = {"input_tokens": int(model.tokenize(payload["texts"])["attention_mask"].sum())}
        elif operation == "transcription":
            from qwen_asr import Qwen3ASRModel
            model = Qwen3ASRModel.from_pretrained(path, dtype=torch.float32 if device == "cpu" else torch.float16,
                device_map=device, attn_implementation="sdpa", max_inference_batch_size=1, max_new_tokens=512)
            loaded = time.monotonic()
            decode_warnings = []
            audio = decode(payload["source"], warnings=decode_warnings)
            audio_seconds = len(audio) / 16000
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
            result = {"text": "\n".join(s["text"] for s in segments), "segments": segments, "warnings": decode_warnings}
        elif operation == "speaker_matching":
            model = speaker_model(path, device)
            loaded = time.monotonic()
            first = voice_embedding(model, decode(payload["source"]), device)
            second = voice_embedding(model, decode(payload["reference"]), device)
            # 相似性，不是校准的身份概率。
            result = {"score": max(0.0, min(1.0, float(torch.dot(first, second))))}
        elif operation == "diarization":
            model = speaker_model(path, device)
            loaded = time.monotonic()
            audio = decode(payload["source"])
            embeddings = []
            for segment in payload["segments"]:
                sample = audio[int(segment["start_time"] * 16000):int(segment["end_time"] * 16000)]
                if len(sample) < 16000:
                    embeddings.append(None)
                    continue
                embeddings.append(voice_embedding(model, sample, device).tolist())
            speakers = cluster_speaker_embeddings(embeddings, payload["segments"])
            result = {"speakers": speakers, "unassigned_segments": sum(speaker is None for speaker in speakers)}
        else:
            raise ValueError("Unknown inference operation")
        return {"result": result, "usage": usage, "audio_seconds": audio_seconds, "diagnostics": {"requested_device": requested, "actual_device": device,
                "fallback_reason": "CUDA_UNAVAILABLE" if requested == "cuda" and device == "cpu" else None,
                "load_seconds": loaded - started, "inference_seconds": time.monotonic() - loaded,
                "peak_memory_bytes": max(peak[0], process.memory_info().rss), "operation": operation}}
    finally:
        stop.set()


if __name__ == "__main__":
    request = json.loads(sys.stdin.buffer.read())
    # 第三方进度/日志记录绝不能破坏协议或泄漏到 API 错误。
    with contextlib.redirect_stdout(sys.stderr):
        try:
            response = run(request)
        except (ImportError, ModuleNotFoundError):
            response = {"error_code": "LOCAL_RUNTIME_DEPENDENCY_MISSING", "message": "本地模型运行依赖不完整，请重新运行安装脚本。"}
        except Exception as exc:
            # 只有设备故障才允许主机在新的 CPU 进程中重试一次。
            import torch
            cuda_failure = isinstance(exc, CudaInitializationError)
            cuda_oom = request.get("_actual_device") == "cuda:0" and isinstance(exc, torch.cuda.OutOfMemoryError)
            if cuda_failure or cuda_oom:
                response = {"error_code": "LOCAL_CUDA_OOM" if cuda_oom else "LOCAL_CUDA_INIT_FAILED",
                            "message": "CUDA 运行失败，将释放进程并重试 CPU。"}
            else:
                response = {"error_code": "LOCAL_INFERENCE_FAILED", "message": "本地推理失败，请检查媒体格式、模型和设备配置。"}
    if "error_code" in response:
        response["diagnostics"] = {"requested_device": request["config"]["device"], "actual_device": request.get("_actual_device", "unknown")}
    from protocol import response_lines
    for line in response_lines(response, request['operation']):
        sys.stdout.buffer.write(line.encode('utf-8'))
