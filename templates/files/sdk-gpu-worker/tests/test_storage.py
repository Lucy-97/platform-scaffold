"""
sdk-gpu-worker storage 模块单元测试
====================================
覆盖: build_r2_object_url、upload_file_to_r2_sync (mock 模式)、
      _get_s3_client 单例、download_from_r2_sync 异常。
"""

import os
import pytest
import tempfile

from gpu_worker.config import BaseWorkerSettings
from gpu_worker.storage import (
    build_r2_object_url,
    upload_file_to_r2_sync,
    download_from_r2_sync,
    _get_s3_client,
)
import gpu_worker.storage as storage_module


# ── Fixtures ──

@pytest.fixture
def settings():
    """纯内存测试配置，不读取 .env 文件"""
    return BaseWorkerSettings(
        WORKER_ID="test-worker-storage",
        SUPPORTED_TASKS=["comfyui_workflow"],
        Agent Core_API_BASE="http://testapi:8011/internal",
        WORKER_SECRET="test-secret",
        R2_ENDPOINT="https://r2.example.com",
        R2_ACCESS_KEY="ak",
        R2_SECRET_KEY="sk",
        R2_BUCKET="",  # 空 bucket 触发 mock 模式
        R2_CUSTOM_DOMAIN="",
        _env_file=None,
    )


@pytest.fixture
def settings_with_domain():
    """带自定义域名的配置"""
    return BaseWorkerSettings(
        WORKER_ID="test-worker-cdn",
        SUPPORTED_TASKS=[],
        Agent Core_API_BASE="http://testapi:8011/internal",
        WORKER_SECRET="test-secret",
        R2_ENDPOINT="https://r2.example.com",
        R2_ACCESS_KEY="ak",
        R2_SECRET_KEY="sk",
        R2_BUCKET="",
        R2_CUSTOM_DOMAIN="https://cdn.aigc.com",
        _env_file=None,
    )


@pytest.fixture(autouse=True)
def reset_s3_client_cache():
    """每个测试前重置 S3 client 单例缓存"""
    storage_module._s3_client_cache = None
    yield
    storage_module._s3_client_cache = None


# ═══════════════════════════════════════════════════════
# 1. build_r2_object_url
# ═══════════════════════════════════════════════════════

class TestGetR2PublicUrl:
    def test_with_endpoint_no_custom_domain(self, settings):
        """无自定义域名时应使用 R2_ENDPOINT 拼接"""
        url = build_r2_object_url("aigc-outputs/abc123.png", settings)
        assert url == "https://r2.example.com/aigc-outputs/abc123.png"

    def test_with_custom_domain(self, settings_with_domain):
        """有自定义域名时应优先使用"""
        url = build_r2_object_url("aigc-outputs/abc123.png", settings_with_domain)
        assert url == "https://cdn.aigc.com/aigc-outputs/abc123.png"

    def test_strips_trailing_slash(self):
        """域名末尾的斜杠应被清除"""
        s = BaseWorkerSettings(
            R2_ENDPOINT="https://r2.example.com/",
            R2_CUSTOM_DOMAIN="https://cdn.aigc.com/",
            _env_file=None,
        )
        url = build_r2_object_url("key.png", s)
        assert url == "https://cdn.aigc.com/key.png"
        assert "//" not in url.split("://")[1]  # 不应有双斜杠

    def test_nested_r2_key(self, settings):
        """支持嵌套路径的 r2_key"""
        url = build_r2_object_url("aigc-outputs/2026/05/19/hash.mp4", settings)
        assert url == "https://r2.example.com/aigc-outputs/2026/05/19/hash.mp4"


# ═══════════════════════════════════════════════════════
# 2. upload_file_to_r2_sync (mock 模式)
# ═══════════════════════════════════════════════════════

class TestUploadFileToR2SyncMock:
    def test_returns_mock_url_when_no_bucket(self, settings, tmp_path):
        """R2_BUCKET 为空时应返回 mock URL 且不真正上传"""
        test_file = tmp_path / "test.png"
        test_file.write_bytes(b"fake png data")

        url = upload_file_to_r2_sync(
            str(test_file), "aigc-outputs/hash123.png", "image/png", settings
        )

        assert "mock-r2.aigc.com" in url
        assert "aigc-outputs/hash123.png" in url

    def test_mock_url_preserves_r2_key(self, settings, tmp_path):
        """Mock URL 应包含完整的 r2_key"""
        test_file = tmp_path / "video.mp4"
        test_file.write_bytes(b"fake video")

        url = upload_file_to_r2_sync(
            str(test_file), "aigc-outputs/video_hash.mp4", "video/mp4", settings
        )
        assert "aigc-outputs/video_hash.mp4" in url


# ═══════════════════════════════════════════════════════
# 3. download_from_r2_sync 异常处理
# ═══════════════════════════════════════════════════════

class TestDownloadFromR2Sync:
    def test_raises_on_no_bucket(self, settings):
        """R2_BUCKET 为空时应抛出 ValueError"""
        with pytest.raises(ValueError, match="R2 Bucket not configured"):
            download_from_r2_sync("some/key.png", "/tmp/output.png", settings)


# ═══════════════════════════════════════════════════════
# 4. _get_s3_client 单例
# ═══════════════════════════════════════════════════════

class TestGetS3Client:
    def test_singleton_returns_same_instance(self, settings):
        """连续调用应返回同一个 client 实例"""
        # 需要有 R2_BUCKET 来避免 mock 路径
        s = BaseWorkerSettings(
            R2_ENDPOINT="https://r2.example.com",
            R2_ACCESS_KEY="ak",
            R2_SECRET_KEY="sk",
            R2_BUCKET="test-bucket",
            R2_CUSTOM_DOMAIN="",
            _env_file=None,
        )
        client_a = _get_s3_client(s)
        client_b = _get_s3_client(s)
        assert client_a is client_b

    def test_returns_boto3_client(self, settings):
        """应返回 boto3 S3 client 类型"""
        client = _get_s3_client(settings)
        # boto3 client 类型是动态生成的，检查方法是否存在
        assert hasattr(client, 'put_object')
        assert hasattr(client, 'upload_file')
        assert hasattr(client, 'download_file')


# ═══════════════════════════════════════════════════════
# 5. report_task_complete outputs 字段
# ═══════════════════════════════════════════════════════

class TestReportTaskCompleteOutputs:
    @pytest.mark.asyncio
    async def test_outputs_defaults_to_empty_list(self, settings):
        """outputs 未传时应序列化为空数组 []"""
        import json
        import respx
        import httpx

        with respx.mock:
            route = respx.post("http://testapi:8011/internal/tasks/complete").mock(
                return_value=httpx.Response(200, json={"status": "ok"})
            )

            from gpu_worker.api import report_task_complete
            await report_task_complete("task-x", "success", settings)

            body = json.loads(route.calls.last.request.content)
            assert body["outputs"] == []
            assert isinstance(body["outputs"], list)

    @pytest.mark.asyncio
    async def test_outputs_passes_through(self, settings):
        """outputs 传入时应原样透传"""
        import json
        import respx
        import httpx

        outputs = [
            {"cache_key": "abc.png", "r2_key": "aigc-outputs/abc.png", "url": "https://cdn/abc.png", "media_type": "image/png", "file_size": 1024}
        ]

        with respx.mock:
            route = respx.post("http://testapi:8011/internal/tasks/complete").mock(
                return_value=httpx.Response(200, json={"status": "ok"})
            )

            from gpu_worker.api import report_task_complete
            await report_task_complete("task-y", "success", settings, outputs=outputs)

            body = json.loads(route.calls.last.request.content)
            assert len(body["outputs"]) == 1
            assert body["outputs"][0]["cache_key"] == "abc.png"
            assert body["outputs"][0]["file_size"] == 1024
