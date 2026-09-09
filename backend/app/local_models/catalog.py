"""经过审核的模型标识；运行时绝不解析浮动的模型版本。"""
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class ModelSpec:
    key: str
    name: str
    capability: str
    repository: str
    revision: str
    license: str
    source: str = "huggingface"
    dimensions: int | None = None

    def public(self):
        return asdict(self)


CATALOG = {
    spec.key: spec for spec in [
        ModelSpec("bekko", "Bekko Embedding v1 A8M", "embedding", "hotchpotch/bekko-embedding-v1-a8m",
                  "c721113d59a1d91b447450324f51c4b3332c924a", "MIT", dimensions=384),
        ModelSpec("granite", "Granite Embedding 97M Multilingual r2", "embedding", "ibm-granite/granite-embedding-97m-multilingual-r2",
                  "835ad14087e140460703cf0fae09f97d469d65c2", "Apache-2.0", dimensions=384),
        ModelSpec("qwen3-asr", "Qwen3 ASR 0.6B", "transcription", "Qwen/Qwen3-ASR-0.6B",
                  "5eb144179a02acc5e5ba31e748d22b0cf3e303b0", "Apache-2.0"),
        ModelSpec("eres2netv2", "ERes2NetV2 中文声纹", "speaker_matching", "iic/speech_eres2netv2_sv_zh-cn_16k-common",
                  "3317286545c587ae682dbc166831d9448780eebb", "Apache-2.0", source="modelscope", dimensions=192),
    ]
}
