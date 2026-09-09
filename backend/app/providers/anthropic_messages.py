"""原生 Anthropic Messages 协议，支持增量解码内容块。"""

import json
from contextlib import aclosing

from app.contracts import MessageRole, ModelEventType, ModelRequest
from app.providers.base import ProviderError, ProviderToolCall, ProviderTurn
from app.providers.http_base import (
    UsageTracker, check_error, decode_tool_arguments, invalid_response, list_value,
    object_value, string_value, token_count, truncated_stream,
)
from app.providers.openai_compatible import OpenAICompatibleProvider
from app.providers.tool_names import mapped_tool_names


class AnthropicMessagesProvider(OpenAICompatibleProvider):
    stream_path = "/messages"

    def _headers(self) -> dict[str, str]:
        headers = super()._headers()
        authorization = headers.pop("Authorization", None)
        if authorization:
            headers["x-api-key"] = authorization.removeprefix("Bearer ")
        headers["anthropic-version"] = "2023-06-01"
        return headers

    def _payload(self, request: ModelRequest, *, stream: bool) -> dict[str, object]:
        systems = [request.system] if request.system else []
        messages = []
        for message in request.messages:
            if message.role == MessageRole.system:
                systems.append(message.content)
                continue
            if message.role == MessageRole.tool:
                if not message.tool_call_id:
                    raise ProviderError("PROVIDER_INVALID_REQUEST", "Tool result requires a call identifier.")
                role = "user"
                content = [{"type": "tool_result", "tool_use_id": message.tool_call_id, "content": message.content}]
            else:
                role = message.role.value
                content = [{"type": "text", "text": message.content}] if message.content else []
                for uri in message.images:
                    header, data = uri.split(",", 1)
                    content.append({"type":"image", "source":{"type":"base64", "media_type":header[5:].split(";")[0], "data":data}})
                content += [{"type": "tool_use", "id": call.tool_call_id, "name": call.name,
                             "input": call.arguments} for call in message.tool_calls]
            if not content:
                continue
            if messages and messages[-1]["role"] == role:
                messages[-1]["content"].extend(content)
            else:
                messages.append({"role": role, "content": content})
        payload: dict[str, object] = {"model": request.model, "messages": messages,
                                     "max_tokens": request.max_tokens or 4096, "stream": stream}
        if systems:
            payload["system"] = "\n\n".join(systems)
        if request.tools:
            payload["tools"] = [{"name": tool.name, "description": tool.description,
                                 "input_schema": tool.parameters} for tool in request.tools]
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.response_format is not None:
            format_ = request.response_format
            if format_.get("type") != "json_schema":
                raise ProviderError("PROVIDER_INVALID_REQUEST", "Messages requires a JSON schema response format.")
            schema = object_value(format_.get("json_schema"))
            payload["output_config"] = {"format": {"type": "json_schema", "schema": object_value(schema.get("schema"))}}
        return payload

    @mapped_tool_names
    async def complete(self, request: ModelRequest) -> ProviderTurn:
        data = await self._request("POST", self.stream_path, json=self._payload(request, stream=False))
        texts = []
        calls = []
        for raw in list_value(data.get("content")):
            block = object_value(raw)
            if block.get("type") == "text":
                texts.append(string_value(block.get("text")))
            elif block.get("type") == "tool_use":
                calls.append(ProviderToolCall(
                    tool_call_id=string_value(block.get("id"), nonempty=True),
                    name=string_value(block.get("name"), nonempty=True),
                    arguments=decode_tool_arguments(block.get("input")),
                ))
        return ProviderTurn(text="".join(texts) or None, tool_calls=calls,
                            **UsageTracker(cache_tokens=True).update(data.get("usage") or {}))

    async def _events(self, request: ModelRequest):
        blocks: dict[int, dict] = {}
        usage = UsageTracker(cache_tokens=True)
        started = False
        async with aclosing(self._stream_json(self._payload(request, stream=True))) as chunks:
            async for data in chunks:
                kind = string_value(data.get("type"), nonempty=True)
                if kind == "message_start":
                    if started:
                        raise invalid_response()
                    started = True
                    message = object_value(data.get("message"))
                    check_error(message)
                    if message.get("usage") is not None:
                        yield ModelEventType.usage, usage.update(message["usage"])
                elif kind == "content_block_start":
                    index = token_count(data.get("index"))
                    if not started or index in blocks:
                        raise invalid_response()
                    block = dict(object_value(data.get("content_block")))
                    blocks[index] = block
                    block["closed"] = False
                    if block.get("type") == "tool_use":
                        block["id"] = string_value(block.get("id"), nonempty=True)
                        block["name"] = string_value(block.get("name"), nonempty=True)
                        block["arguments"] = ""
                        block["input"] = object_value(block.get("input", {}))
                        yield ModelEventType.tool_call_start, {"tool_call_id": block["id"], "name": block["name"]}
                    elif block.get("type") == "text" and block.get("text"):
                        yield ModelEventType.text_delta, {"text": string_value(block["text"])}
                    elif block.get("type") == "thinking" and block.get("thinking"):
                        yield ModelEventType.thinking_delta, {"text": string_value(block["thinking"])}
                elif kind == "content_block_delta":
                    block = blocks.get(token_count(data.get("index")))
                    if block is None or block["closed"]:
                        raise invalid_response()
                    delta = object_value(data.get("delta"))
                    delta_type = delta.get("type")
                    if delta_type == "text_delta":
                        if block.get("type") != "text":
                            raise invalid_response()
                        yield ModelEventType.text_delta, {"text": string_value(delta.get("text"))}
                    elif delta_type == "thinking_delta":
                        if block.get("type") != "thinking":
                            raise invalid_response()
                        yield ModelEventType.thinking_delta, {"text": string_value(delta.get("thinking"))}
                    elif delta_type == "input_json_delta" and block.get("type") == "tool_use":
                        fragment = string_value(delta.get("partial_json"))
                        block["arguments"] += fragment
                        yield ModelEventType.tool_call_delta, {"tool_call_id": block["id"], "arguments_delta": fragment}
                    # 签名和未来​​的增量类型在 ModelEvent 中没有表示。
                elif kind == "content_block_stop":
                    block = blocks.get(token_count(data.get("index")))
                    if block is None or block["closed"]:
                        raise invalid_response()
                    block["closed"] = True
                    if block.get("type") == "tool_use":
                        if block["arguments"]:
                            decode_tool_arguments(block["arguments"])
                        else:
                            yield ModelEventType.tool_call_delta, {
                                "tool_call_id": block["id"], "arguments_delta": json.dumps(block["input"]),
                            }
                        yield ModelEventType.tool_call_end, {"tool_call_id": block["id"]}
                elif kind == "message_delta":
                    if not started:
                        raise invalid_response()
                    object_value(data.get("delta"))
                    if data.get("usage") is not None:
                        yield ModelEventType.usage, usage.update(data["usage"])
                elif kind == "message_stop":
                    if not started:
                        raise invalid_response()
                    if any(not block["closed"] for block in blocks.values()):
                        raise truncated_stream()
                    return
                elif kind == "[DONE]":
                    raise truncated_stream()
        raise truncated_stream()
