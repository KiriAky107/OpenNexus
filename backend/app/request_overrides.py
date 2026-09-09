"""声明性请求主体扩展与显式主机拥有的字段冲突。"""
import copy
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

PROTECTED = {"model", "messages", "input", "system", "instructions", "tools", "tool_choice", "parallel_tool_calls",
             "functions", "function_call", "file", "audio", "reference_file", "stream", "previous_response_id",
             "conversation", "background", "store"}
SECRETS = {"api_key", "apikey", "authorization", "headers", "url", "base_url", "access_token", "secret", "password"}


class RequestOverride(BaseModel):
    model_config = ConfigDict(extra="forbid")
    capability: Literal["chat", "embedding", "transcription", "speaker_matching"] = "chat"
    model: str | None = Field(default=None, max_length=200)
    stream: bool | None = None
    body: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def valid_mode(self):
        if self.capability != "chat" and self.stream is True:
            raise ValueError("当前 Embedding 与媒体接口不使用流式请求")
        return self

    @field_validator("body")
    @classmethod
    def validate_body(cls, value):
        if len(json.dumps(value, allow_nan=False).encode()) > 32768:
            raise ValueError("自定义请求 JSON 不得超过 32 KiB")
        conflicts = PROTECTED.intersection(value)
        if conflicts:
            raise ValueError("运行请求管理字段不可覆盖：" + ", ".join(sorted(conflicts)))
        def check(item, depth=0):
            if depth > 12:
                raise ValueError("JSON 嵌套不得超过 12 层")
            if isinstance(item, dict):
                if any(str(k).lower().replace("-", "_") in SECRETS for k in item):
                    raise ValueError("密钥、Header 和 URL 请使用独立配置，不得放入请求 JSON")
                for child in item.values():
                    check(child, depth + 1)
            elif isinstance(item, list):
                for child in item:
                    check(child, depth + 1)
        check(value)
        if "stream_options" in value:
            options = value["stream_options"]
            if not isinstance(options, dict) or ("include_usage" in options and type(options["include_usage"]) is not bool):
                raise ValueError("stream_options 必须是对象，include_usage 必须是布尔值")
        return value


def deep_merge(base, extension):
    result = copy.deepcopy(base)
    for key, value in extension.items():
        result[key] = deep_merge(result[key], value) if isinstance(value, dict) and isinstance(result.get(key), dict) else copy.deepcopy(value)
    return result


def apply_overrides(payload, rules, capability, *, stream=False):
    selected = [rule for rule in rules if rule.capability == capability and rule.model in (None, payload.get("model"))
                and (rule.stream is None or rule.stream == stream)]
    # 一般默认值先于模型覆盖；显式流条件是最具体的。
    selected.sort(key=lambda rule: (rule.model is not None, rule.stream is not None))
    for rule in selected:
        payload = deep_merge(payload, rule.body)
    return payload
