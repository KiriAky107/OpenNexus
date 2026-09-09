"""本机 /responses 适配器；无状态历史记录使用 function_call/输出项。"""

import json
from contextlib import aclosing

from app.contracts import MessageRole, ModelEventType, ModelRequest
from app.providers.base import ProviderError, ProviderToolCall, ProviderTurn
from app.providers.http_base import (
    UsageTracker, check_error, decode_tool_arguments, invalid_response, list_value,
    object_value, remote_error, string_value, token_count, truncated_stream,
)
from app.providers.openai_compatible import OpenAICompatibleProvider
from app.providers.tool_names import mapped_tool_names


class OpenAIResponsesProvider(OpenAICompatibleProvider):
    stream_path = "/responses"

    def _payload(self, request: ModelRequest, *, stream: bool) -> dict[str, object]:
        inputs = []
        for message in request.messages:
            if message.role == MessageRole.tool:
                if not message.tool_call_id:
                    raise ProviderError("PROVIDER_INVALID_REQUEST", "Tool result requires a call identifier.")
                inputs.append({"type": "function_call_output", "call_id": message.tool_call_id,
                               "output": message.content})
                continue
            if message.content or not message.tool_calls:
                inputs.append({"role": message.role.value, "content": ([{"type":"input_text","text":message.content}] + [{"type":"input_image","image_url":uri} for uri in message.images]) if message.images else message.content})
            for call in message.tool_calls:
                inputs.append({"type": "function_call", "call_id": call.tool_call_id,
                               "name": call.name, "arguments": json.dumps(call.arguments)})
        payload: dict[str, object] = {"model": request.model, "input": inputs, "stream": stream}
        if request.system:
            payload["instructions"] = request.system
        if request.tools:
            payload["tools"] = [{"type": "function", "name": tool.name,
                                 "description": tool.description, "parameters": tool.parameters}
                                for tool in request.tools]
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.max_tokens is not None:
            payload["max_output_tokens"] = request.max_tokens
        if request.response_format is not None:
            format_ = dict(request.response_format)
            if format_.get("type") == "json_schema":
                format_ = {"type": "json_schema", **object_value(format_.get("json_schema"))}
            payload["text"] = {"format": format_}
        return payload

    @staticmethod
    def _check_response(data: dict) -> None:
        check_error(data)
        status = data.get("status")
        if status == "incomplete":
            raise ProviderError("PROVIDER_INCOMPLETE_RESPONSE", "Provider response is incomplete.")
        if status == "failed":
            raise remote_error(data.get("error"))
        if status is not None and status != "completed":
            raise invalid_response()

    @mapped_tool_names
    async def complete(self, request: ModelRequest) -> ProviderTurn:
        data = await self._request("POST", self.stream_path, json=self._payload(request, stream=False))
        self._check_response(data)
        texts = []
        calls = []
        for raw in list_value(data.get("output")):
            item = object_value(raw)
            if item.get("type") == "message":
                for raw_part in list_value(item.get("content")):
                    part = object_value(raw_part)
                    if part.get("type") == "output_text":
                        texts.append(string_value(part.get("text")))
                    elif part.get("type") == "refusal":
                        texts.append(string_value(part.get("refusal")))
            elif item.get("type") == "function_call":
                calls.append(ProviderToolCall(
                    tool_call_id=string_value(item.get("call_id"), nonempty=True),
                    name=string_value(item.get("name"), nonempty=True),
                    arguments=decode_tool_arguments(item.get("arguments")),
                ))
        return ProviderTurn(text="".join(texts) or None, tool_calls=calls,
                            **UsageTracker().update(data.get("usage") or {}))

    async def _events(self, request: ModelRequest):
        calls: dict[int, dict] = {}
        usage = UsageTracker()

        def finish_call(index: int, final: object = None):
            call = calls[index]
            if call["ended"]:
                return []
            events = []
            if final is not None:
                arguments = string_value(final)
                if not arguments.startswith(call["arguments"]):
                    raise invalid_response()
                remainder = arguments[len(call["arguments"]):]
                if remainder:
                    events.append((ModelEventType.tool_call_delta,
                                   {"tool_call_id": call["id"], "arguments_delta": remainder}))
                call["arguments"] = arguments
            decode_tool_arguments(call["arguments"])
            call["ended"] = True
            events.append((ModelEventType.tool_call_end, {"tool_call_id": call["id"]}))
            return events

        async with aclosing(self._stream_json(self._payload(request, stream=True))) as chunks:
            async for data in chunks:
                kind = string_value(data.get("type"), nonempty=True)
                if kind in {"response.failed", "response.incomplete"}:
                    response = object_value(data.get("response"))
                    self._check_response({**response, "status": kind.split(".")[1]})
                elif kind in {"response.output_text.delta", "response.refusal.delta"}:
                    yield ModelEventType.text_delta, {"text": string_value(data.get("delta"))}
                elif kind in {"response.reasoning_summary_text.delta", "response.reasoning_text.delta"}:
                    yield ModelEventType.thinking_delta, {"text": string_value(data.get("delta"))}
                elif kind in {"response.output_item.added", "response.output_item.done"}:
                    item = object_value(data.get("item"))
                    if item.get("type") != "function_call":
                        continue
                    index = token_count(data.get("output_index"))
                    call_id = string_value(item.get("call_id"), nonempty=True)
                    name = string_value(item.get("name"), nonempty=True)
                    if index not in calls:
                        calls[index] = {"id": call_id, "name": name, "arguments": "", "ended": False,
                                        "item_id": item.get("id")}
                        yield ModelEventType.tool_call_start, {"tool_call_id": call_id, "name": name}
                    elif calls[index]["id"] != call_id or calls[index]["name"] != name:
                        raise invalid_response()
                    if kind == "response.output_item.done":
                        for event in finish_call(index, item.get("arguments")):
                            yield event
                    elif item.get("arguments"):
                        arguments = string_value(item["arguments"])
                        calls[index]["arguments"] += arguments
                        yield ModelEventType.tool_call_delta, {"tool_call_id": call_id, "arguments_delta": arguments}
                elif kind in {"response.function_call_arguments.delta", "response.function_call_arguments.done"}:
                    index = token_count(data.get("output_index"))
                    call = calls.get(index)
                    if call is None or (data.get("item_id") and call["item_id"] != data["item_id"]):
                        raise invalid_response()
                    if kind.endswith(".done"):
                        for event in finish_call(index, data.get("arguments")):
                            yield event
                    else:
                        if call["ended"]:
                            raise invalid_response()
                        fragment = string_value(data.get("delta"))
                        call["arguments"] += fragment
                        yield ModelEventType.tool_call_delta, {"tool_call_id": call["id"], "arguments_delta": fragment}
                elif kind == "response.completed":
                    response = object_value(data.get("response"))
                    self._check_response(response)
                    if any(not call["ended"] for call in calls.values()):
                        raise truncated_stream()
                    if response.get("usage") is not None:
                        yield ModelEventType.usage, usage.update(response["usage"])
                    return
                elif kind == "[DONE]":
                    raise truncated_stream()
                elif kind in {"response.created", "response.in_progress"}:
                    response = object_value(data.get("response"))
                    check_error(response)
                    if response.get("usage") is not None:
                        yield ModelEventType.usage, usage.update(response["usage"])
        raise truncated_stream()
