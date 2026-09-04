"""Explicit real-model smoke: run with the backend Python, never part of unit tests."""
import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.local_models.manager import _download, read_state
from app.local_models.runtime import runtime


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("model", choices=["bekko", "granite", "qwen3-asr", "eres2netv2"])
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--audio")
    parser.add_argument("--reference")
    args = parser.parse_args()
    if args.download:
        await _download(args.model)
    state = read_state(args.model)
    print(json.dumps(state), flush=True)
    if state["status"] != "installed":
        raise SystemExit(1)
    if args.model in {"bekko", "granite"}:
        result = await runtime.infer(args.model, "embedding", {"texts": ["今天上课学习线性代数", "矩阵与向量是线性代数的基础", "晚餐吃番茄炒蛋"]})
        print(json.dumps({"count": len(result), "dimensions": len(result[0]),
                          "related_similarity": sum(a * b for a, b in zip(result[0], result[1])),
                          "unrelated_similarity": sum(a * b for a, b in zip(result[0], result[2]))}))
    elif args.audio:
        operation = "transcription" if args.model == "qwen3-asr" else "speaker_matching"
        result = await runtime.infer(args.model, operation, {"source": str(Path(args.audio).resolve()),
            "language": "zh", "reference": str(Path(args.reference or args.audio).resolve())})
        print(json.dumps(result, ensure_ascii=False))
    print(json.dumps(runtime.diagnostics), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
