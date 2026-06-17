import os
import time
import pytest
import shutil
import asyncio
import base64
from pathlib import Path
from unittest.mock import MagicMock, patch, ANY

from gpu_worker.config import BaseWorkerSettings
from gpu_worker.storage import (
    upload_with_thumbnail_sync,
    upload_to_r2
)

# 1x1 透明 PNG 的真实合法 base64 数据
VALID_PNG_BYTES = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=")

@pytest.fixture
def settings():
    return BaseWorkerSettings(
        WORKER_ID="test-worker-regression",
        SUPPORTED_TASKS=["bg_removal", "comfyui_workflow"],
        Agent Core_API_BASE="http://testapi:8011/internal",
        WORKER_SECRET="test-secret",
        R2_ENDPOINT="https://r2.test.com",
        R2_ACCESS_KEY="ak",
        R2_SECRET_KEY="sk",
        R2_BUCKET="test-bucket-regression",
        R2_CUSTOM_DOMAIN="",
        _env_file=None,
    )

def test_upload_with_thumbnail_regression(settings, tmp_path):
    """
    回归测试：验证 upload_with_thumbnail_sync 异步上传大文件
    以及在原始文件被快速清理的情况下，大文件依然从安全备份目录成功上传的逻辑。
    """
    mock_s3 = MagicMock()
    
    # 1. 建立模拟目录结构 (模拟 output 目录)
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    
    # 创建一个合法的 PNG 图片模拟文件
    large_file = output_dir / "large_artwork.png"
    large_file.write_bytes(VALID_PNG_BYTES)
    
    r2_key = "aigc-outputs/artwork123.png"
    content_type = "image/png"
    
    with patch("gpu_worker.storage._get_s3_client", return_value=mock_s3):
        # 2. 调用优化后的极速上传接口
        start_t = time.time()
        url, thumb_url, thumb_r2_key = upload_with_thumbnail_sync(
            str(large_file), r2_key, content_type, settings
        )
        elapsed = time.time() - start_t
        
        # 验证该同步函数是极速响应（因为大图是后台上传）
        assert elapsed < 0.2
        assert "artwork123.png" in url
        assert "thumbs/aigc-outputs/artwork123.png.webp" in thumb_url
        
        # 3. 极其恶劣情况模拟：立刻清空 output 目录（模拟下一个任务 WorkspaceManager.setup 触发的清理）
        shutil.rmtree(output_dir)
        assert not large_file.exists()
        
        # 4. 等待后台线程池完成上传
        time.sleep(0.5)
        
        # 5. 校验 R2 对象存储端是否确实收到了缩略图和大图的上传请求
        mock_s3.put_object.assert_any_call(
            Bucket=settings.R2_BUCKET,
            Key=thumb_r2_key,
            Body=ANY,
            ContentType='image/webp'
        )
        
        mock_s3.upload_file.assert_called_once()
        args, kwargs = mock_s3.upload_file.call_args
        
        # 验证上传的文件源路径确实已被转换，并且已被清理
        uploaded_filename = kwargs['Filename']
        assert uploaded_filename != str(large_file)
        assert ".bg_uploads" in uploaded_filename
        assert not os.path.exists(uploaded_filename) # 应该已经被 unlink 成功清理了
        
        assert kwargs['Bucket'] == settings.R2_BUCKET
        assert kwargs['Key'] == r2_key

@pytest.mark.asyncio
async def test_upload_to_r2_async_regression(settings):
    """
    回归测试：验证 async upload_to_r2 函数能够极速响应，并且大图在后台线程池中异步完成上传。
    """
    mock_s3 = MagicMock()
    task_id = "task-async-img"
    
    with patch("gpu_worker.storage._get_s3_client", return_value=mock_s3):
        start_t = time.time()
        url, thumb_url, thumb_r2_key = await upload_to_r2(task_id, VALID_PNG_BYTES, settings)
        elapsed = time.time() - start_t
        
        # 验证该异步函数瞬间返回
        assert elapsed < 0.2
        assert task_id in url
        
        # 等待后台线程执行完毕
        await asyncio.sleep(0.5)
        
        # s3.put_object 应该被调用了两次：一次是同步的缩略图上传，一次是后台大图上传
        assert mock_s3.put_object.call_count == 2
