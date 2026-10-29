"""LLMClient — 统一封装模型请求、超时、错误与返回结果

模型不再写死在 .env 里，而是由 model_configs 表驱动（见 model_config_service）：
  - mock：本地模拟，不依赖网络与 Key，用于链路验证
  - deepseek / local / custom：统一走 OpenAI 兼容的 /chat/completions 接口
差异点：本地模型通常无需 api_key，且首次加载模型较慢，超时时间单独放宽。
"""
from __future__ import annotations

import asyncio
import math
import time
from dataclasses import dataclass, field

import httpx

from app.core.config import settings
from app.core.exceptions import ModelCallException

MOCK_MODEL = "mock"

# 字符数估算口径：约 1.5 个汉字 ≈ 1 token（英文单词更少字符，粗估足够用于统计）
CHARS_PER_TOKEN = 1.5


@dataclass(slots=True)
class ModelTarget:
    """一次模型调用所需的全部参数（由 model_config_service 从数据库解析而来）

    api_key 是解密后的明文，只存在于内存中，不会出现在任何响应里。
    """

    name: str                              # 配置标识（/api/chat 的 model_name）
    provider: str                           # mock / deepseek / local / custom
    base_url: str | None = None
    upstream_model: str | None = None
    api_key: str = field(default="", repr=False)   # repr 排除，避免日志打印密钥
    timeout: int = 10


@dataclass(slots=True)
class ModelResult:
    """一次模型调用的结果，直接对应 api_call_logs 需要落库的字段"""

    answer: str
    latency_ms: int
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


def estimate_tokens(text: str) -> int:
    """按字符数粗估 token：mock 模型与上游未返回 usage 时的兜底口径"""
    if not text:
        return 0
    return max(1, math.ceil(len(text) / CHARS_PER_TOKEN))


async def chat(question: str, target: ModelTarget) -> ModelResult:
    """调用模型，返回回答、耗时与 Token 用量；失败抛 ModelCallException"""
    started = time.perf_counter()
    if target.provider == MOCK_MODEL or target.name == MOCK_MODEL:
        answer = await _mock_answer(question)
        prompt_tokens = estimate_tokens(question)
        completion_tokens = estimate_tokens(answer)
    else:
        answer, prompt_tokens, completion_tokens = await _openai_compatible(
            question, target
        )
    # 取整到毫秒，写入 api_call_logs.latency_ms
    latency_ms = int((time.perf_counter() - started) * 1000)
    return ModelResult(
        answer=answer,
        latency_ms=latency_ms,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=prompt_tokens + completion_tokens,
    )


async def _mock_answer(question: str) -> str:
    """mock 模型：固定模板回答，用于本地链路验证

    刻意 sleep 50ms：模拟真实网络耗时，保证 latency_ms 有值可验证。
    """
    await asyncio.sleep(0.05)
    return f"这是 mock 模型的回答：{question}（当前为本地联调模式，未调用真实大模型）"


async def _openai_compatible(question: str, target: ModelTarget) -> tuple[str, int, int]:
    """真实模型分支：DeepSeek、本地 Ollama/vLLM、其他自定义服务共用此实现

    返回 (answer, prompt_tokens, completion_tokens)。
    """
    url = (target.base_url or "").rstrip("/") + "/chat/completions"
    payload = {
        "model": target.upstream_model or target.name,
        "messages": [{"role": "user", "content": question}],
        "stream": False,   # 本期一次性返回；SSE 流式属后续迭代
    }
    headers = {"Content-Type": "application/json"}
    # 本地模型多为免鉴权，只有配了 Key 才带 Authorization
    if target.api_key:
        headers["Authorization"] = f"Bearer {target.api_key}"

    # 超时与网络异常统一映射为 502，前端只需处理一种错误码
    # connect 单独设 5s：本地服务没启动时快速失败，不必等满整个读超时
    timeout = httpx.Timeout(target.timeout, connect=5.0)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, json=payload, headers=headers)
    except httpx.ConnectTimeout as exc:
        # 本地服务未启动时走这里：5 秒连接超时，与"推理慢"区分开
        raise ModelCallException(
            f"模型 {target.name} 连接超时（5s），请确认 {target.base_url} 可访问"
        ) from exc
    except httpx.TimeoutException as exc:
        raise ModelCallException(
            f"模型 {target.name} 响应超时（{target.timeout}s）"
        ) from exc
    except httpx.HTTPError as exc:
        # 本地模型最常见的场景：服务没启动，连接被拒绝
        raise ModelCallException(
            f"模型 {target.name} 连接失败：{exc.__class__.__name__}，"
            f"请确认 {target.base_url} 可访问"
        ) from exc

    # 区分上游服务故障（5xx）与请求被拒（4xx，多为 Key 或额度问题）
    if resp.status_code >= 500:
        raise ModelCallException(f"模型服务返回 {resp.status_code}")
    if resp.status_code >= 400:
        raise ModelCallException(
            f"模型调用被拒绝：{resp.status_code}（请检查 API Key 与模型名）"
        )

    # 上游把错误塞在 200 响应体里也时有发生，这里按"格式异常"兜底
    try:
        data = resp.json()
        answer = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, ValueError, TypeError) as exc:
        raise ModelCallException("模型返回格式异常，请确认服务为 OpenAI 兼容接口") from exc

    # usage 是 Token 统计的权威来源；部分兼容服务不返回，按字符估算兜底
    usage = data.get("usage") or {}
    prompt_tokens = int(usage.get("prompt_tokens") or 0) or estimate_tokens(question)
    completion_tokens = (
        int(usage.get("completion_tokens") or 0) or estimate_tokens(answer)
    )
    return answer, prompt_tokens, completion_tokens
