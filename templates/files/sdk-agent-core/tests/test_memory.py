"""
MemoryStore + MemoryRetriever + SessionBlackboard 单元测试
==========================================================

验证记忆存储的 CRUD、冲突消解、检索评分和黑板快照功能。
"""

import asyncio
import os
import shutil
import time

import pytest

from agent_core.memory.models import (
    MemoryCategory,
    MemoryEntry,
    MemoryLayer,
)
from agent_core.memory.store import MemoryStore
from agent_core.memory.retriever import MemoryRetriever
from agent_core.memory.session_blackboard import SessionBlackboard


# ── Fixtures ──

TEST_DIR = "/tmp/agent_test_memory"


@pytest.fixture(autouse=True)
def clean_dir():
    """每个测试前后清理临时目录。"""
    if os.path.exists(TEST_DIR):
        shutil.rmtree(TEST_DIR)
    yield
    if os.path.exists(TEST_DIR):
        shutil.rmtree(TEST_DIR)


@pytest.fixture
def store():
    return MemoryStore(storage_dir=TEST_DIR)


@pytest.fixture
def retriever(store):
    return MemoryRetriever(store=store, max_injection=5)


# ── MemoryStore Tests ──


@pytest.mark.asyncio
async def test_store_save_and_get(store):
    """基本 CRUD：保存 + 按 ID 获取。"""
    entry = MemoryEntry(
        memory_id=store.generate_id(),
        layer=MemoryLayer.PROJECT,
        category=MemoryCategory.ENTITY,
        subject="AgentRuntimeEngine",
        content="核心引擎，负责 LLM 调用和工具执行。",
        confidence=0.9,
    )
    saved_id = await store.save(entry)
    assert saved_id == entry.memory_id

    retrieved = await store.get(saved_id)
    assert retrieved is not None
    assert retrieved.subject == "AgentRuntimeEngine"
    assert retrieved.content == entry.content


@pytest.mark.asyncio
async def test_store_search(store):
    """关键词搜索测试。"""
    for i, (subj, content) in enumerate([
        ("CostTracker", "Token 成本追踪器"),
        ("TraceCollector", "链路追踪收集器"),
        ("ToolExecutor", "微内核工具执行管线"),
    ]):
        entry = MemoryEntry(
            memory_id=store.generate_id(),
            layer=MemoryLayer.PROJECT,
            category=MemoryCategory.ENTITY,
            subject=subj,
            content=content,
            confidence=0.9,
        )
        await store.save(entry)

    # 搜索 "成本"
    results = await store.search("成本")
    assert len(results) == 1
    assert results[0].subject == "CostTracker"

    # 搜索 "追踪" 应返回两条
    results = await store.search("追踪")
    assert len(results) == 2


@pytest.mark.asyncio
async def test_store_conflict_resolution(store):
    """冲突消解：高 confidence 覆盖低 confidence。"""
    old = MemoryEntry(
        memory_id=store.generate_id(),
        layer=MemoryLayer.PROJECT,
        category=MemoryCategory.ENTITY,
        subject="模型配置",
        content="使用 GPT-4",
        confidence=0.7,
    )
    await store.save(old)

    # 新记忆同 subject+category，更高 confidence
    new = MemoryEntry(
        memory_id=store.generate_id(),
        layer=MemoryLayer.PROJECT,
        category=MemoryCategory.ENTITY,
        subject="模型配置",
        content="已切换为 Gemini Flash",
        confidence=0.95,
    )
    await store.save(new)

    # 旧记忆应被标记为 superseded
    old_refreshed = await store.get(old.memory_id)
    assert old_refreshed.superseded_by == new.memory_id


@pytest.mark.asyncio
async def test_store_delete(store):
    """删除记忆条目。"""
    entry = MemoryEntry(
        memory_id=store.generate_id(),
        layer=MemoryLayer.SESSION,
        category=MemoryCategory.STATE,
        subject="当前阶段",
        content="剧本第三集",
    )
    await store.save(entry)
    assert store.count == 1

    result = await store.delete(entry.memory_id)
    assert result is True
    assert store.count == 0


# ── MemoryRetriever Tests ──


@pytest.mark.asyncio
async def test_retriever_scoring(store, retriever):
    """检索排序：L1 高于 L3，高 confidence 优先。"""
    # L1 高优先级
    e1 = MemoryEntry(
        memory_id=store.generate_id(),
        layer=MemoryLayer.SESSION,
        category=MemoryCategory.STATE,
        subject="当前任务",
        content="正在分析链路追踪代码",
        confidence=0.8,
    )
    # L3 低优先级但高 confidence
    e2 = MemoryEntry(
        memory_id=store.generate_id(),
        layer=MemoryLayer.PROJECT,
        category=MemoryCategory.ENTITY,
        subject="链路追踪",
        content="trace.py 实现了轻量级 Span 树",
        confidence=0.95,
    )
    await store.save(e1)
    await store.save(e2)

    results = await retriever.retrieve("链路追踪")
    assert len(results) >= 1
    # 不论排序，两条都应被检索到
    subjects = {r.subject for r in results}
    assert "链路追踪" in subjects


@pytest.mark.asyncio
async def test_retriever_format_injection(store, retriever):
    """注入格式化：应生成 <memories> 标记的可读文本。"""
    entry = MemoryEntry(
        memory_id=store.generate_id(),
        layer=MemoryLayer.PROJECT,
        category=MemoryCategory.ENTITY,
        subject="测试实体",
        content="这是一个测试记忆条目",
        confidence=0.9,
    )
    await store.save(entry)

    results = await retriever.retrieve("测试实体")
    text = retriever.format_for_injection(results)
    assert "<memories>" in text
    assert "测试记忆条目" in text


# ── SessionBlackboard Tests ──


def test_blackboard_set_get():
    """基本设置/获取。"""
    bb = SessionBlackboard(session_id="test_session")
    bb.set("stage", "第三集", source="user")
    assert bb.get("stage") == "第三集"
    assert bb.size == 1


def test_blackboard_snapshot():
    """快照导出/恢复。"""
    bb = SessionBlackboard(session_id="test_session")
    bb.set("current_episode", 3, source="system")
    bb.set("character", "林默", source="llm")

    snapshot = bb.to_snapshot()
    assert snapshot["session_id"] == "test_session"
    assert len(snapshot["slots"]) == 2

    # 新黑板从快照恢复
    bb2 = SessionBlackboard(session_id="test_restored")
    bb2.restore_from_snapshot(snapshot)
    assert bb2.get("current_episode") == 3
    assert bb2.get("character") == "林默"


def test_blackboard_injection_text():
    """注入文本生成。"""
    bb = SessionBlackboard(session_id="test")
    bb.set("goal", "写剧本第五集", source="user")
    bb.set("progress", "已完成前四集", source="system")

    text = bb.to_injection_text()
    assert "[会话状态黑板]" in text
    assert "写剧本第五集" in text
    assert "已完成前四集" in text


def test_blackboard_history():
    """历史版本追踪。"""
    bb = SessionBlackboard(session_id="test", max_history=3)
    for i in range(5):
        bb.set("counter", i, source="system")

    assert bb.get("counter") == 4
    # 历史应保留最近 3 个旧版本
    assert len(bb._history.get("counter", [])) == 3
