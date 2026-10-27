"""LLMClient — 统一封装模型请求、超时、错误与返回结果

本期提供两个模型：
  - mock：本地链路验证，不依赖网络与 Key
  - settings.DEFAULT_MODEL：真实模型，走 OpenAI 兼容的 /chat/completions
"""
from __future__ import annotations

import asyncio
import time

import httpx

from app.core.config import settings
from app.core.exceptions import ModelCallException, ModelNotFoundException

MOCK_MODEL = "mock"


def list_models() -> list[dict[str, str]]:
    """可用模型列表：mock 必在，并至少返回一个真实模型名"""
    models = [{"model_name": MOCK_MODEL, "display_name": "Mock 模型"}]
    if settings.DEFAULT_MODEL and settings.DEFAULT_MODEL != MOCK_MODEL:
        models.append({
            "model_name": settings.DEFAULT_MODEL,
            "display_name": settings.DEFAULT_MODEL,
        })
    return models


def is_known_model(model_name: str) -> bool:
    """判断模型名是否在可用列表中，未知模型直接拒绝，避免把请求打到错误地址"""
    return any(m["model_name"] == model_name for m in list_models())


async def chat(question: str, model_name: str) -> tuple[str, int]:
    """调用模型，返回 (answer, latency_ms)；失败抛 ModelCallException"""
    if not is_known_model(model_name):
        # detail 带回可选模型名，前端可直接提示"请从以下模型中选择"
        raise ModelNotFoundException(
            f"模型 {model_name} 不存在",
            detail={"available_models": [m["model_name"] for m in list_models()]},
        )

    started = time.perf_counter()
    if model_name == MOCK_MODEL:
        answer = await _mock_answer(question)
    else:
        answer = await _real_answer(question, model_name)
    # 取整到毫秒，写入 api_call_logs.latency_ms
    latency_ms = int((time.perf_counter() - started) * 1000)
    return answer, latency_ms


async def _mock_answer(question: str) -> str:
    """mock 模型：固定模板回答，用于本地链路验证

    刻意 sleep 50ms：模拟真实网络耗时，保证 latency_ms 有值可验证。
    """
    await asyncio.sleep(0.05)
    return f"这是 mock 模型的回答：{question}（当前为本地联调模式，未调用真实大模型）"


async def _real_answer(question: str, model_name: str) -> str:
    """真实模型分支：走 OpenAI 兼容的 /chat/completions 接口"""
    # Key 缺失属于部署配置问题，提前给明确提示，而不是发一个必然 401 的请求
    if not settings.MODEL_API_KEY:
        raise ModelCallException("模型 API Key 未配置，请先在 .env 中设置 MODEL_API_KEY")

    url = settings.MODEL_BASE_URL.rstrip("/") + "/chat/completions"
    payload = {
        "model": model_name,
        "messages": [{"role": "user", "content": question}],
        "stream": False,   # P0 用一次性返回；SSE 流式属 P1
    }
    headers = {"Authorization": f"Bearer {settings.MODEL_API_KEY}"}

    # 超时与网络异常统一映射为 502，前端只需处理一种错误码
    try:
        async with httpx.AsyncClient(timeout=settings.MODEL_TIMEOUT_SECONDS) as client:
            resp = await client.post(url, json=payload, headers=headers)
    except httpx.TimeoutException as exc:
        raise ModelCallException("模型调用超时") from exc
    except httpx.HTTPError as exc:
        raise ModelCallException(f"模型调用失败：{exc.__class__.__name__}") from exc

    # 区分上游服务故障（5xx）与请求被拒（4xx，多为 Key 或额度问题）
    if resp.status_code >= 500:
        raise ModelCallException(f"模型服务返回 {resp.status_code}")
    if resp.status_code >= 400:
        raise ModelCallException(f"模型调用被拒绝：{resp.status_code}")

    # 上游把错误塞在 200 响应体里也时有发生，这里按"格式异常"兜底
    try:
        data = resp.json()
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, ValueError) as exc:
        raise ModelCallException("模型返回格式异常") from exc
