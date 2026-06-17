"""
后台任务编排模块单元测试
========================

使用 fakeredis 模拟 Redis，无需实际 Redis 服务即可运行。
覆盖 TaskStore / TaskOutputStream / CascadingCancel / Notification 全链路。

运行方式：
    cd agent-core
    pip install pytest pytest-asyncio fakeredis
    python -m pytest tests/test_orchestration.py -v
"""

import asyncio
import json

import pytest
import pytest_asyncio

# fakeredis 模拟 Redis（无需实际 Redis 服务）
import fakeredis.aioredis


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def redis():
    """创建 fakeredis 异步实例。"""
    r = fakeredis.aioredis.FakeRedis()
    yield r
    await r.flushall()
    await r.aclose()


# ---------------------------------------------------------------------------
# 1. TaskState 模型测试
# ---------------------------------------------------------------------------

class TestTaskState:
    """AgentTaskState / AgentTaskType / is_terminal 测试."""

    def test_generate_task_id_prefix(self):
        """ID 前缀应与任务类型对应。"""
        from agent_core.orchestration.task_state import AgentTaskType, generate_task_id

        cr_id = generate_task_id(AgentTaskType.COMFYUI_RENDER)
        assert cr_id.startswith("cr_"), f"ComfyUI ID 应以 cr_ 开头: {cr_id}"

        sa_id = generate_task_id(AgentTaskType.SUB_AGENT)
        assert sa_id.startswith("sa_"), f"SubAgent ID 应以 sa_ 开头: {sa_id}"

        bj_id = generate_task_id(AgentTaskType.BASH_JOB)
        assert bj_id.startswith("bj_"), f"Bash ID 应以 bj_ 开头: {bj_id}"

    def test_is_terminal(self):
        """终态判断应正确区分运行态和终态。"""
        from agent_core.orchestration.task_state import AgentTaskStatus, is_terminal

        assert not is_terminal(AgentTaskStatus.PENDING)
        assert not is_terminal(AgentTaskStatus.RUNNING)
        assert is_terminal(AgentTaskStatus.COMPLETED)
        assert is_terminal(AgentTaskStatus.FAILED)
        assert is_terminal(AgentTaskStatus.KILLED)

    def test_auto_output_stream_key(self):
        """output_stream_key 应自动填充。"""
        from agent_core.orchestration.task_state import (
            AgentTaskType, AgentTaskState, generate_task_id,
        )

        tid = generate_task_id(AgentTaskType.TTS_SYNTHESIS)
        task = AgentTaskState(
            id=tid,
            task_type=AgentTaskType.TTS_SYNTHESIS,
            description="测试 TTS 合成",
        )
        assert task.output_stream_key == f"agent:task_output:{tid}"


# ---------------------------------------------------------------------------
# 2. TaskStore 测试
# ---------------------------------------------------------------------------

class TestTaskStore:
    """TaskStore Redis Hash 持久化测试。"""

    @pytest.mark.asyncio
    async def test_register_and_get(self, redis):
        """注册任务后应能查询到。"""
        from agent_core.orchestration import TaskStore, AgentTaskType, AgentTaskState, generate_task_id

        store = TaskStore(redis)
        tid = generate_task_id(AgentTaskType.COMFYUI_RENDER)
        task = AgentTaskState(
            id=tid,
            task_type=AgentTaskType.COMFYUI_RENDER,
            description="渲染角色分镜",
            project_id="proj_001",
        )
        await store.register(task)

        retrieved = await store.get(tid)
        assert retrieved is not None
        assert retrieved.id == tid
        assert retrieved.task_type == AgentTaskType.COMFYUI_RENDER
        assert retrieved.description == "渲染角色分镜"
        assert retrieved.project_id == "proj_001"

    @pytest.mark.asyncio
    async def test_update_status(self, redis):
        """更新状态后终态应正确设置。"""
        from agent_core.orchestration import (
            TaskStore, AgentTaskType, AgentTaskStatus,
            AgentTaskState, generate_task_id, is_terminal,
        )

        store = TaskStore(redis)
        tid = generate_task_id(AgentTaskType.BASH_JOB)
        task = AgentTaskState(
            id=tid,
            task_type=AgentTaskType.BASH_JOB,
            description="ffmpeg 视频拼接",
        )
        await store.register(task)

        # 更新为运行态
        await store.update_status(tid, AgentTaskStatus.RUNNING)
        t = await store.get(tid)
        assert t.status == AgentTaskStatus.RUNNING
        assert not is_terminal(t.status)

        # 更新为完成态
        await store.update_status(
            tid, AgentTaskStatus.COMPLETED,
            result_summary="视频拼接完成，总时长 3:45",
        )
        t = await store.get(tid)
        assert t.status == AgentTaskStatus.COMPLETED
        assert is_terminal(t.status)
        assert t.result_summary == "视频拼接完成，总时长 3:45"
        assert t.end_time is not None

    @pytest.mark.asyncio
    async def test_completed_unnotified(self, redis):
        """get_completed_unnotified 应只返回终态且未通知的任务。"""
        from agent_core.orchestration import (
            TaskStore, AgentTaskType, AgentTaskStatus,
            AgentTaskState, generate_task_id,
        )

        store = TaskStore(redis)
        project = "proj_002"

        # 创建三个任务：1个运行中、1个完成未通知、1个完成已通知
        t_running = AgentTaskState(
            id=generate_task_id(AgentTaskType.SUB_AGENT),
            task_type=AgentTaskType.SUB_AGENT,
            description="审查子Agent（运行中）",
            project_id=project,
        )
        t_completed = AgentTaskState(
            id=generate_task_id(AgentTaskType.TTS_SYNTHESIS),
            task_type=AgentTaskType.TTS_SYNTHESIS,
            description="TTS 合成（完成未通知）",
            project_id=project,
        )
        t_notified = AgentTaskState(
            id=generate_task_id(AgentTaskType.COMFYUI_RENDER),
            task_type=AgentTaskType.COMFYUI_RENDER,
            description="渲染（完成已通知）",
            project_id=project,
        )

        for t in [t_running, t_completed, t_notified]:
            await store.register(t)

        await store.update_status(t_running.id, AgentTaskStatus.RUNNING)
        await store.update_status(
            t_completed.id, AgentTaskStatus.COMPLETED,
            result_summary="TTS 完成",
        )
        await store.update_status(
            t_notified.id, AgentTaskStatus.COMPLETED,
            result_summary="渲染完成",
        )
        await store.mark_notified(t_notified.id)

        # 应只返回 t_completed
        unnotified = await store.get_completed_unnotified(project)
        assert len(unnotified) == 1
        assert unnotified[0].id == t_completed.id


# ---------------------------------------------------------------------------
# 3. TaskOutputStream 测试
# ---------------------------------------------------------------------------

class TestTaskOutputStream:
    """Redis Stream 输出管道测试。"""

    @pytest.mark.asyncio
    async def test_append_and_read(self, redis):
        """写入输出后应能增量读取。"""
        from agent_core.orchestration import TaskOutputStream

        stream = TaskOutputStream(redis, "test_task_001")

        # 追加 3 条输出
        id1 = await stream.append("开始渲染...", level="info")
        id2 = await stream.append("进度 50%", level="progress")
        id3 = await stream.append("渲染完成", level="info")

        # 全量读取
        entries = await stream.read_since("0-0")
        assert len(entries) == 3
        assert entries[0]["content"] == "开始渲染..."
        assert entries[1]["level"] == "progress"

        # 增量读取（从 id2 之后）
        new_entries = await stream.read_since(entries[1]["id"])
        assert len(new_entries) == 1
        assert new_entries[0]["content"] == "渲染完成"

    @pytest.mark.asyncio
    async def test_cleanup_sets_ttl(self, redis):
        """cleanup 应设置过期时间。"""
        from agent_core.orchestration import TaskOutputStream

        stream = TaskOutputStream(redis, "test_task_ttl")
        await stream.append("test")
        await stream.cleanup(ttl_seconds=3600)

        ttl = await redis.ttl(stream.stream_key)
        assert 0 < ttl <= 3600


# ---------------------------------------------------------------------------
# 4. CascadingCancel 测试
# ---------------------------------------------------------------------------

class TestCascadingCancel:
    """级联取消链测试。"""

    def test_basic_cancel(self):
        """基本取消应正常工作。"""
        from agent_core.orchestration import CascadingCancel

        cc = CascadingCancel()
        assert not cc.is_cancelled
        cc.cancel()
        assert cc.is_cancelled

    def test_cascade_sync(self):
        """父取消时应级联取消子级（同步链路）。"""
        from agent_core.orchestration import CascadingCancel

        root = CascadingCancel()
        child_a = root.create_child()
        child_b = root.create_child()
        grandchild = child_a.create_child()

        # 取消前，全部不应被取消
        assert not root.is_cancelled
        assert not child_a.is_cancelled
        assert not grandchild.is_cancelled

        # 取消根
        root.cancel()

        # 全部应级联取消
        assert root.is_cancelled
        assert child_a.is_cancelled
        assert child_b.is_cancelled
        assert grandchild.is_cancelled

    def test_partial_cancel(self):
        """取消子级不应影响父级和兄弟。"""
        from agent_core.orchestration import CascadingCancel

        root = CascadingCancel()
        child_a = root.create_child()
        child_b = root.create_child()
        grandchild = child_a.create_child()

        # 只取消 child_a
        child_a.cancel()

        assert not root.is_cancelled       # 父不受影响
        assert not child_b.is_cancelled     # 兄弟不受影响
        assert child_a.is_cancelled
        assert grandchild.is_cancelled      # 子级被级联


# ---------------------------------------------------------------------------
# 5. Notification 测试
# ---------------------------------------------------------------------------

class TestNotification:
    """JSON 结果回注测试。"""

    def test_build_notification_format(self):
        """通知消息应为合法的 JSON user message。"""
        from agent_core.orchestration import (
            build_task_notification, AgentTaskType, AgentTaskStatus,
            AgentTaskState, generate_task_id,
        )

        task = AgentTaskState(
            id=generate_task_id(AgentTaskType.TTS_SYNTHESIS),
            task_type=AgentTaskType.TTS_SYNTHESIS,
            status=AgentTaskStatus.COMPLETED,
            description="角色A 语音合成",
            result_summary="生成 wav 文件 3.2MB",
        )

        msg = build_task_notification(task)
        assert msg["role"] == "user"
        assert "[系统通知]" in msg["content"]

        # 应能从 content 中提取合法 JSON
        json_part = msg["content"].split("\n", 1)[1]
        payload = json.loads(json_part)
        assert payload["task_id"] == task.id
        assert payload["status"] == "completed"
        assert payload["result"] == "生成 wav 文件 3.2MB"

    @pytest.mark.asyncio
    async def test_inject_completed_notifications(self, redis):
        """inject_completed_notifications 应注入通知并标记已通知。"""
        from agent_core.orchestration import (
            TaskStore, AgentTaskType, AgentTaskStatus,
            AgentTaskState, generate_task_id,
            inject_completed_notifications,
        )

        store = TaskStore(redis)
        project = "proj_inject"

        task = AgentTaskState(
            id=generate_task_id(AgentTaskType.COMFYUI_RENDER),
            task_type=AgentTaskType.COMFYUI_RENDER,
            description="渲染测试",
            project_id=project,
        )
        await store.register(task)
        await store.update_status(
            task.id, AgentTaskStatus.COMPLETED,
            result_summary="渲染完成",
        )

        messages = [{"role": "user", "content": "请渲染分镜"}]
        count = await inject_completed_notifications(messages, store, project)

        # 应注入 1 条通知
        assert count == 1
        assert len(messages) == 2
        assert "[系统通知]" in messages[1]["content"]

        # 再次调用不应重复注入
        count2 = await inject_completed_notifications(messages, store, project)
        assert count2 == 0
        assert len(messages) == 2


# ---------------------------------------------------------------------------
# 6. 模块导入完整性测试
# ---------------------------------------------------------------------------

class TestModuleImport:
    """确保 orchestration 包可正常导入。"""

    def test_all_exports(self):
        """__all__ 中的所有名称应可导入。"""
        from agent_core import orchestration
        for name in orchestration.__all__:
            assert hasattr(orchestration, name), f"缺少导出: {name}"
