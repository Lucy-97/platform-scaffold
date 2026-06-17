"""
AgentRuntimeEngine 核心路径单元测试
=====================================

使用 mock LLM 验证引擎事件流、Hook 生命周期、
CostTracker/TraceCollector 集成和 Memory/Compaction 自动注册。
"""

import asyncio
import json
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent_core.runtime.engine import AgentRuntimeEngine
from agent_core.runtime.events import RuntimeEvent, RuntimeEventType
from agent_core.runtime.hooks import HookPhase, LifecycleHookRegistry
from agent_core.runtime.cost_tracker import CostTracker
from agent_core.runtime.trace import TraceCollector


# ── Helper: 模拟 litellm 流式响应 ──

def _make_mock_chunk(content: str, finish_reason=None, usage=None):
    """构造一个模拟的 litellm streaming chunk。"""
    delta = MagicMock()
    delta.content = content
    delta.tool_calls = None

    choice = MagicMock()
    choice.delta = delta
    choice.finish_reason = finish_reason

    chunk = MagicMock()
    chunk.choices = [choice]
    chunk.usage = usage
    return chunk


def _make_usage(prompt=100, completion=50):
    """构造模拟 usage 对象。"""
    u = MagicMock()
    u.prompt_tokens = prompt
    u.completion_tokens = completion
    u.total_tokens = prompt + completion
    return u


async def _mock_stream_response(*chunks):
    """异步生成器——模拟流式响应。"""
    for c in chunks:
        yield c


# ── Tests ──


@pytest.mark.asyncio
async def test_submit_basic_stream():
    """基本流式输出：应产生 STREAM_DELTA + RESULT 事件。"""
    usage = _make_usage(100, 50)
    chunks = [
        _make_mock_chunk("你好"),
        _make_mock_chunk("世界"),
        _make_mock_chunk("", finish_reason="stop", usage=usage),
    ]
    mock_response = _mock_stream_response(*chunks)

    with patch("litellm.acompletion", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = mock_response

        engine = AgentRuntimeEngine(
            model="test/mock-model",
            api_key="fake-key",
        )

        events: List[RuntimeEvent] = []
        async for event in engine.submit("你好"):
            events.append(event)

    types = [e.type for e in events]
    # 应有流式片段
    assert RuntimeEventType.STREAM_DELTA in types
    # 应有最终结果
    assert RuntimeEventType.RESULT in types

    # STREAM_DELTA 至少出现一次
    deltas = [e for e in events if e.type == RuntimeEventType.STREAM_DELTA]
    assert len(deltas) >= 1


@pytest.mark.asyncio
async def test_hooks_lifecycle_order():
    """Hook 生命周期：PRE_SAMPLING 应在 POST_SAMPLING 之前触发。"""
    call_order: List[str] = []

    async def pre_hook(**kw):
        call_order.append("pre_sampling")

    async def post_hook(**kw):
        call_order.append("post_sampling")

    hooks = LifecycleHookRegistry()
    hooks.register(HookPhase.PRE_SAMPLING, pre_hook, priority=10)
    hooks.register(HookPhase.POST_SAMPLING, post_hook, priority=10)

    usage = _make_usage(50, 30)
    chunks = [
        _make_mock_chunk("OK", finish_reason="stop", usage=usage),
    ]

    with patch("litellm.acompletion", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = _mock_stream_response(*chunks)

        engine = AgentRuntimeEngine(
            model="test/mock", api_key="x", hooks=hooks,
        )
        async for _ in engine.submit("test"):
            pass

    assert "pre_sampling" in call_order
    assert "post_sampling" in call_order
    assert call_order.index("pre_sampling") < call_order.index("post_sampling")


@pytest.mark.asyncio
async def test_cost_tracker_records():
    """CostTracker 应从 usage 累加 Token 计数。"""
    usage = _make_usage(200, 100)
    chunks = [
        _make_mock_chunk("结果", finish_reason="stop", usage=usage),
    ]

    cost_tracker = CostTracker()

    with patch("litellm.acompletion", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = _mock_stream_response(*chunks)

        engine = AgentRuntimeEngine(
            model="test/mock", api_key="x",
            cost_tracker=cost_tracker,
        )
        async for _ in engine.submit("test"):
            pass

    summary = cost_tracker.get_summary()
    assert summary["calls"] >= 1
    assert summary["total_tokens"] >= 300  # prompt(200) + completion(100)


@pytest.mark.asyncio
async def test_trace_span_creation():
    """TraceCollector 应创建至少一个 LLM Span。"""
    usage = _make_usage()
    chunks = [
        _make_mock_chunk("OK", finish_reason="stop", usage=usage),
    ]

    trace = TraceCollector()

    with patch("litellm.acompletion", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = _mock_stream_response(*chunks)

        engine = AgentRuntimeEngine(
            model="test/mock", api_key="x",
            trace_collector=trace,
        )

        events = []
        async for event in engine.submit("test"):
            events.append(event)

    # Trace 应有至少一个 Span
    trace_obj = trace.get_trace()
    assert len(trace_obj.spans) >= 1

    # 应有 TRACE_COMPLETE 事件
    trace_events = [e for e in events if e.type == RuntimeEventType.TRACE_COMPLETE]
    assert len(trace_events) == 1


@pytest.mark.asyncio
async def test_engine_with_memory_params():
    """传入 memory 参数后应自动注册 MemoryHooks 到 PRE_SAMPLING。"""
    from agent_core.memory.store import MemoryStore
    from agent_core.memory.retriever import MemoryRetriever

    store = MemoryStore(storage_dir="/tmp/agent_test_engine_mem")
    retriever = MemoryRetriever(store=store)

    engine = AgentRuntimeEngine(
        model="test/mock", api_key="x",
        memory_store=store,
        memory_retriever=retriever,
    )

    # Hook 注册表应含有 memory 的 Hook（summary 返回 dict）
    summary = engine.hooks.summary()
    assert "pre_sampling" in summary
    # pre_sampling 的 Hook 名列表中应有 memory 相关的
    pre_hooks = summary.get("pre_sampling", [])
    assert any("retrieval" in name.lower() for name in pre_hooks)


@pytest.mark.asyncio
async def test_engine_with_compaction_params():
    """传入 compaction 参数后应自动注册 CompactionHooks。"""
    from agent_core.compaction.budget import AgentCoreTokenBudget

    budget = AgentCoreTokenBudget(project_id="test", total_budget=10000)

    engine = AgentRuntimeEngine(
        model="test/mock", api_key="x",
        compaction_budget=budget,
    )

    # Hook 注册表应有 compaction 的 Hook
    summary = engine.hooks.summary()
    assert "pre_sampling" in summary
    pre_hooks = summary.get("pre_sampling", [])
    assert any("compaction" in name.lower() for name in pre_hooks)
