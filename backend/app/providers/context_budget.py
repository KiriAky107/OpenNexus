"""Opt-in, model-scoped text context checks. Estimates are not vendor token counts."""
import json
import math

from app.contracts import Message, MessageRole, ModelRequest
from app.providers.base import ProviderError


def estimate(request):
    # Include system, tool schemas and call arguments. A conservative UTF-8 heuristic
    # still cannot replace the model's tokenizer or account for hidden reasoning.
    body = {"system": request.system, "messages": [m.model_dump(mode="json") for m in request.messages],
            "tools": [t.model_dump(mode="json") for t in request.tools], "format": request.response_format}
    return math.ceil(len(json.dumps(body, ensure_ascii=False).encode("utf-8")) / 2) + 64


async def prepare_context(request, config, complete, *, stream=False):
    policy = next((p for p in config.context_policies if p.model == request.model), None)
    if policy is None:
        return request
    request = request.model_copy(update={"max_tokens": request.max_tokens or policy.output_reserve}, deep=True)
    from app.request_overrides import apply_overrides
    overrides = apply_overrides({"model": request.model}, config.request_overrides, "chat", stream=stream)
    def output_limits(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key in {"max_tokens", "max_completion_tokens", "max_output_tokens", "num_predict", "thinking_budget", "budget_tokens"}:
                    if type(child) is not int or child < 1:
                        raise ProviderError("CONTEXT_CONFIG_CONFLICT", "上下文检测需要明确的正整数输出预算，请检查自定义请求参数。")
                    yield child
                elif isinstance(child, dict):
                    yield from output_limits(child)
    reserve = max(policy.output_reserve, request.max_tokens or 0, sum(output_limits(overrides)))
    budget = policy.context_window - reserve
    if budget <= 0:
        raise ProviderError("CONTEXT_CONFIG_CONFLICT", "输出及思考预算已占满上下文窗口，请调整模型上下文配置。")
    if request.attachments:
        raise ProviderError("CONTEXT_ESTIMATE_UNSUPPORTED", "当前上下文检测只支持文本；附件 Token 无法可靠估算，请关闭该模型的检测或移除附件。")
    before = estimate(request)
    if before < budget * policy.threshold:
        return request
    message = f"上下文估算约 {before:,} Token，输入预算 {budget:,}，已达到 {policy.threshold:.0%} 阈值。"
    if policy.mode == "detect":
        raise ProviderError("CONTEXT_COMPRESSION_REQUIRED", message + " 请在 Provider 表单启用历史摘要压缩，或新建对话。")
    # Only compact completed plain-text turns. Tool chains have protocol-specific
    # reasoning state; never split them or silently discard their signed content.
    if any(m.tool_calls or m.role == MessageRole.tool for m in request.messages):
        raise ProviderError("CONTEXT_COMPRESSION_UNSUPPORTED", message + " 工具调用历史需完整保留，请新建对话。")
    users = [i for i, m in enumerate(request.messages) if m.role == MessageRole.user]
    split = users[-2] if len(users) >= 3 else (users[-1] if len(users) >= 2 else 0)
    if not split:
        raise ProviderError("CONTEXT_COMPRESSION_REQUIRED", message + " 没有可压缩的旧对话，请缩短当前输入。")
    history = [m for m in request.messages[:split] if m.role != MessageRole.system]
    systems = [m for m in request.messages if m.role == MessageRole.system]
    retained = [m for m in request.messages[split:] if m.role != MessageRole.system]
    if estimate(request.model_copy(update={"messages": systems + retained})) >= budget:
        raise ProviderError("CONTEXT_COMPRESSION_REQUIRED", message + " 最近对话本身已超预算，请缩短输入。")
    summary_request = ModelRequest(provider_id=request.provider_id, model=request.model,
        system=policy.prompt, messages=[Message(role=MessageRole.user,
            content=json.dumps([m.model_dump(mode="json") for m in history], ensure_ascii=False))],
        max_tokens=min(policy.output_reserve, 2048), metadata={**request.metadata, "purpose": "context_compression"})
    # Detect oversize summarization itself before sending. No truncation or retry loop.
    if estimate(summary_request) + reserve >= policy.context_window:
        raise ProviderError("CONTEXT_COMPRESSION_REQUIRED", message + " 历史过长，摘要请求也会超限，请新建对话或缩短历史。")
    from app.services.usage_service import usage_context
    from uuid import uuid4
    summary_overrides = apply_overrides({"model": request.model}, config.request_overrides, "chat", stream=False)
    summary_reserve = max(reserve, sum(output_limits(summary_overrides)))
    if estimate(summary_request) + summary_reserve >= policy.context_window:
        raise ProviderError("CONTEXT_CONFIG_CONFLICT", "摘要请求的自定义输出预算超限，请调整非流式请求参数。")
    usage_token = usage_context.set({"request_id": uuid4().hex, "run_id": request.metadata.get("run_id")})
    try:
        result = await complete(summary_request)
    finally:
        usage_context.reset(usage_token)
    if not result.text or not result.text.strip() or result.tool_calls:
        raise ProviderError("CONTEXT_COMPRESSION_FAILED", "模型未返回有效摘要，原对话未修改。")
    prepared = request.model_copy(deep=True)
    # Summary is conversation data, never promoted to system instructions.
    prepared.messages = [*systems, Message(role=MessageRole.user, content="历史对话摘要（仅供参考）：\n" + result.text),
                         Message(role=MessageRole.assistant, content="已记录历史摘要。"), *retained]
    if estimate(prepared) >= budget or estimate(prepared) >= before:
        raise ProviderError("CONTEXT_COMPRESSION_FAILED", "压缩后仍超预算或未缩短上下文，原对话未修改。请新建对话。")
    return prepared
