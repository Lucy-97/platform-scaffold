"""
sdk-gpu-worker 单元测试
=======================
使用 respx 模拟 httpx 请求，pytest-asyncio 驱动异步测试。
"""

import asyncio
import json
import pytest
import httpx
import respx

from gpu_worker.config import BaseWorkerSettings
from gpu_worker.api import (
    heartbeat_loop,
    poll_tasks,
    report_task_complete,
    report_task_progress,
    report_task_progress_sync,
)
from gpu_worker.storage import upload_to_r2


# ── Fixtures ──

@pytest.fixture
def settings():
    """创建一个纯内存的测试配置，不读取任何 .env 文件"""
    return BaseWorkerSettings(
        WORKER_ID="test-worker-1",
        SUPPORTED_TASKS=["bg_removal", "comfyui_workflow"],
        Agent Core_API_BASE="http://testapi:8011/internal",
        WORKER_SECRET="test-secret-123",
        R2_ENDPOINT="https://r2.test.com",
        R2_ACCESS_KEY="ak",
        R2_SECRET_KEY="sk",
        R2_BUCKET="",  # 空 bucket 触发 mock 模式
        R2_CUSTOM_DOMAIN="",
        _env_file=None,  # 不读取 .env
    )


# ═══════════════════════════════════════════════════════
# 1. Config 继承测试
# ═══════════════════════════════════════════════════════

class TestBaseWorkerSettings:
    def test_defaults(self):
        """基类默认值应该合理"""
        s = BaseWorkerSettings(_env_file=None)
        assert s.WORKER_ID == "worker-1"
        assert s.SUPPORTED_TASKS == []
        assert s.Agent Core_API_BASE == "http://localhost:8011/internal"
        assert s.R2_BUCKET == ""

    def test_inheritance(self, settings):
        """子类实例应正确继承和覆盖"""
        assert settings.WORKER_ID == "test-worker-1"
        assert "bg_removal" in settings.SUPPORTED_TASKS
        assert settings.WORKER_SECRET == "test-secret-123"


# ═══════════════════════════════════════════════════════
# 2. report_task_complete 测试
# ═══════════════════════════════════════════════════════

class TestReportTaskComplete:
    @pytest.mark.asyncio
    @respx.mock
    async def test_report_success(self, settings):
        """成功上报应发送正确的 payload 和 header"""
        route = respx.post("http://testapi:8011/internal/tasks/complete").mock(
            return_value=httpx.Response(200, json={"status": "ok"})
        )

        await report_task_complete(
            "task-abc", "success", settings,
            result_url="https://r2.test.com/result.png"
        )

        assert route.called
        req = route.calls.last.request
        body = json.loads(req.content)
        assert body["task_id"] == "task-abc"
        assert body["status"] == "success"
        assert body["result_url"] == "https://r2.test.com/result.png"
        assert req.headers["X-Internal-Secret"] == "test-secret-123"

    @pytest.mark.asyncio
    @respx.mock
    async def test_report_failure(self, settings):
        """失败上报应包含 error 字段"""
        route = respx.post("http://testapi:8011/internal/tasks/complete").mock(
            return_value=httpx.Response(200, json={"status": "ok"})
        )

        await report_task_complete(
            "task-abc", "failed", settings,
            error_msg="OOM error"
        )

        body = json.loads(route.calls.last.request.content)
        assert body["status"] == "failed"
        assert body["error"] == "OOM error"

    @pytest.mark.asyncio
    @respx.mock
    async def test_report_network_error(self, settings):
        """网络故障不应抛异常（仅 log）"""
        respx.post("http://testapi:8011/internal/tasks/complete").mock(
            side_effect=httpx.ConnectError("Connection refused")
        )

        # 不应该抛出异常
        await report_task_complete("task-abc", "failed", settings, error_msg="test")


# ═══════════════════════════════════════════════════════
# 3. report_task_progress 测试 (async)
# ═══════════════════════════════════════════════════════

class TestReportTaskProgress:
    @pytest.mark.asyncio
    @respx.mock
    async def test_progress_percentage_calc(self, settings):
        """进度百分比计算应正确：50/200 = 25%"""
        route = respx.post("http://testapi:8011/internal/tasks/progress").mock(
            return_value=httpx.Response(200, json={"status": "ok"})
        )

        await report_task_progress("task-xyz", "KSampler", 50, 200, settings)

        body = json.loads(route.calls.last.request.content)
        assert body["task_id"] == "task-xyz"
        assert body["node"] == "KSampler"
        assert body["percent"] == 25

    @pytest.mark.asyncio
    @respx.mock
    async def test_progress_zero_total(self, settings):
        """total=0 时不应除零错误，应返回 0%"""
        route = respx.post("http://testapi:8011/internal/tasks/progress").mock(
            return_value=httpx.Response(200, json={"status": "ok"})
        )

        await report_task_progress("task-xyz", "Node1", 5, 0, settings)

        body = json.loads(route.calls.last.request.content)
        assert body["percent"] == 0


# ═══════════════════════════════════════════════════════
# 4. report_task_progress_sync 测试
# ═══════════════════════════════════════════════════════

class TestReportTaskProgressSync:
    @respx.mock
    def test_sync_progress(self, settings):
        """同步版本应通过 httpx.Client 正确发送"""
        route = respx.post("http://testapi:8011/internal/tasks/progress").mock(
            return_value=httpx.Response(200, json={"status": "ok"})
        )

        report_task_progress_sync("task-sync", "VAEDecode", 80, 100, settings)

        assert route.called
        body = json.loads(route.calls.last.request.content)
        assert body["percent"] == 80
        assert body["node"] == "VAEDecode"

    @respx.mock
    def test_sync_progress_server_error(self, settings):
        """服务端 500 不应抛异常（仅 log）"""
        respx.post("http://testapi:8011/internal/tasks/progress").mock(
            return_value=httpx.Response(500, text="internal error")
        )

        # 不应抛出异常
        report_task_progress_sync("task-sync", "Node", 1, 10, settings)


# ═══════════════════════════════════════════════════════
# 5. upload_to_r2 测试 (Mock 模式)
# ═══════════════════════════════════════════════════════

class TestUploadToR2:
    @pytest.mark.asyncio
    async def test_mock_upload_when_no_bucket(self, settings):
        """R2_BUCKET 为空时应返回 mock URL 而非真正上传"""
        assert settings.R2_BUCKET == ""

        url, thumb_url, thumb_key = await upload_to_r2("task-mock-123", b"fake-png-bytes", settings)

        assert "mock-r2.aigc.com" in url
        assert "task-mock-123" in url


# ═══════════════════════════════════════════════════════
# 6. poll_tasks 测试
# ═══════════════════════════════════════════════════════

class TestPollTasks:
    @pytest.mark.asyncio
    @respx.mock
    async def test_poll_receives_task_and_calls_handler(self, settings):
        """轮询到任务后应正确调用 handler 回调"""
        received_tasks = []

        async def fake_handler(task_id, task_type, payload):
            received_tasks.append((task_id, task_type, payload))
            raise asyncio.CancelledError()  # 中断循环以结束测试

        task_response = {
            "code": "200",
            "data": {
                "has_task": True,
                "task": {
                    "task_id": "task-poll-1",
                    "task_type": "bg_removal",
                    "payload": {"image_url": "https://example.com/img.png"}
                }
            }
        }

        respx.post("http://testapi:8011/internal/tasks/pop").mock(
            return_value=httpx.Response(200, json=task_response)
        )
        # mock the complete endpoint for the CancelledError fallback
        respx.post("http://testapi:8011/internal/tasks/complete").mock(
            return_value=httpx.Response(200, json={"status": "ok"})
        )

        with pytest.raises(asyncio.CancelledError):
            await poll_tasks(settings, fake_handler)

        assert len(received_tasks) == 1
        assert received_tasks[0][0] == "task-poll-1"
        assert received_tasks[0][1] == "bg_removal"
        assert received_tasks[0][2]["image_url"] == "https://example.com/img.png"

    @pytest.mark.asyncio
    @respx.mock
    async def test_poll_204_no_task(self, settings):
        """204 响应（无任务）应继续轮询，不调用 handler"""
        call_count = 0

        async def should_not_be_called(task_id, task_type, payload):
            nonlocal call_count
            call_count += 1

        # 第一次返回 204，第二次也 204，第三次抛异常中断
        responses = [
            httpx.Response(204),
            httpx.Response(204),
        ]
        route = respx.post("http://testapi:8011/internal/tasks/pop").mock(
            side_effect=responses + [httpx.ConnectError("stop")]
        )

        # poll_tasks 会在 ConnectError 后 sleep 然后继续，我们等一下然后取消
        task = asyncio.create_task(poll_tasks(settings, should_not_be_called))
        await asyncio.sleep(0.5)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        assert call_count == 0

    @pytest.mark.asyncio
    @respx.mock
    async def test_poll_handler_failure_reports_complete(self, settings):
        """handler 抛异常时应自动上报 failed 状态"""
        async def failing_handler(task_id, task_type, payload):
            raise ValueError("Processing exploded")

        task_response = {
            "data": {
                "has_task": True,
                "task": {
                    "task_id": "task-fail-1",
                    "task_type": "bg_removal",
                    "payload": {}
                }
            }
        }

        respx.post("http://testapi:8011/internal/tasks/pop").mock(
            side_effect=[
                httpx.Response(200, json=task_response),
                httpx.ConnectError("stop"),
            ]
        )
        complete_route = respx.post("http://testapi:8011/internal/tasks/complete").mock(
            return_value=httpx.Response(200, json={"status": "ok"})
        )

        task = asyncio.create_task(poll_tasks(settings, failing_handler))
        await asyncio.sleep(0.5)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        # 应该调用了 complete 接口上报 failed
        assert complete_route.called
        body = json.loads(complete_route.calls.last.request.content)
        assert body["status"] == "failed"
        assert "Processing exploded" in body["error"]


# ═══════════════════════════════════════════════════════
# 7. heartbeat_loop 测试
# ═══════════════════════════════════════════════════════

class TestHeartbeatLoop:
    @pytest.mark.asyncio
    @respx.mock
    async def test_heartbeat_sends_correct_payload(self, settings):
        """心跳应包含 worker_id、supported_tasks 和自定义 fingerprint"""
        route = respx.post("http://testapi:8011/internal/workers/heartbeat").mock(
            return_value=httpx.Response(200, json={"status": "ok"})
        )

        def my_fingerprint():
            return {"gpu": "RTX5090"}

        def my_metrics():
            return {"tasks_done": 42}

        task = asyncio.create_task(
            heartbeat_loop(settings, get_fingerprint_fn=my_fingerprint, get_metrics_fn=my_metrics)
        )

        # 等第一次心跳发出
        await asyncio.sleep(0.3)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        assert route.called
        body = json.loads(route.calls.last.request.content)
        assert body["worker_id"] == "test-worker-1"
        assert body["supported_tasks"] == ["bg_removal", "comfyui_workflow"]
        assert body["fingerprint"]["gpu"] == "RTX5090"
        assert body["metrics"]["tasks_done"] == 42
        assert body["status"] == "idle"
