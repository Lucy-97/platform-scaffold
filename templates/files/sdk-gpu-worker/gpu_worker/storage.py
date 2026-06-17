import asyncio
import time
import boto3
import io
import subprocess
import concurrent.futures
import uuid
import shutil
import threading
import atexit
from pathlib import Path
from PIL import Image
# 放宽 Pillow 默认的图像像素限制（从 1.78 亿像素合理放宽至 3 亿像素以安全容纳极限大图超分，同时防御 DOS OOM 攻击）
Image.MAX_IMAGE_PIXELS = 300000000
from botocore.config import Config as BotoConfig
from loguru import logger
from .config import BaseWorkerSettings


_s3_client_cache = None
_s3_client_lock = threading.Lock()

def _get_s3_client(settings: BaseWorkerSettings):
    """
    Lazy initialize and return a singleton boto3 S3 client with double-checked locking for thread safety.
    """
    global _s3_client_cache
    if _s3_client_cache is None:
        with _s3_client_lock:
            if _s3_client_cache is None:
                _s3_client_cache = boto3.client(
                    's3',
                    endpoint_url=settings.R2_ENDPOINT,
                    aws_access_key_id=settings.R2_ACCESS_KEY,
                    aws_secret_access_key=settings.R2_SECRET_KEY,
                    config=BotoConfig(
                        signature_version='s3v4',
                        connect_timeout=10,
                        read_timeout=300,
                        retries={'max_attempts': 3},
                        proxies={}  # 禁用代理，直连 R2（防止继承容器 HTTP_PROXY 环境变量）
                    )
                )
    return _s3_client_cache


def build_r2_object_url(r2_key: str, settings: BaseWorkerSettings) -> str:
    """
    Construct the public URL for an R2 object.
    Centralizes URL construction logic to avoid duplication.
    """
    domain = settings.R2_CUSTOM_DOMAIN.rstrip('/') if settings.R2_CUSTOM_DOMAIN else settings.R2_ENDPOINT.rstrip('/')
    return f"{domain}/{r2_key}"

# 全局后台上传线程池，防范暴线程
_upload_executor = concurrent.futures.ThreadPoolExecutor(
    max_workers=4,
    thread_name_prefix="bg_uploader"
)

def _shutdown_upload_executor():
    # 进程退出时的静默清理，等待当前队列里的上传任务全部完成，防止进程突然死亡截断上传导致永久 404
    # 避免在 atexit 阶段使用 logger 以防系统标准流关闭导致 I/O 报错
    _upload_executor.shutdown(wait=True)

atexit.register(_shutdown_upload_executor)


_cleaned_old_uploads = False

def _clean_old_temp_files(bg_dir: Path):
    global _cleaned_old_uploads
    if _cleaned_old_uploads:
        return
    _cleaned_old_uploads = True
    try:
        if bg_dir.exists():
            now = time.time()
            for item in bg_dir.iterdir():
                # 超过 12 小时的文件判定为孤儿残留临时文件（上一次未正常退出的遗留物）
                if item.is_file() and (now - item.stat().st_mtime > 43200):
                    item.unlink()
                    logger.info(f"[BgUpload] Cleaned up orphaned stale temp file: {item.name}")
    except Exception as e:
        logger.warning(f"[BgUpload] Failed to clean old temp files in {bg_dir}: {e}")

def _get_safe_bg_upload_path(local_path: str) -> Path:
    """
    根据给定的 local_path，生成一个在 output 目录之外、且属于 workspace 安全区域的临时拷贝路径。
    并在首次调用时触发历史孤儿临时文件的自动清理净化。
    """
    path_obj = Path(local_path).resolve()
    # 如果它的父级是 output 目录，我们将临时文件放在 output 的同级（即爷爷目录）下
    if path_obj.parent.name == "output":
        bg_dir = path_obj.parent.parent / ".bg_uploads"
    else:
        bg_dir = path_obj.parent / ".bg_uploads"
        
    bg_dir.mkdir(parents=True, exist_ok=True)
    
    # 自动净化旧的孤儿临时文件
    _clean_old_temp_files(bg_dir)
    
    return bg_dir / f"{uuid.uuid4().hex}{path_obj.suffix}"

def _bg_upload_file_task(temp_path: Path, r2_key: str, content_type: str, settings: BaseWorkerSettings):
    """
    在后台线程中同步上传大文件，并确保最终删除临时副本（带指数退避的应用层重试）。
    """
    max_retries = 3
    delay = 1.0
    try:
        for attempt in range(1, max_retries + 1):
            try:
                start_t = time.time()
                logger.info(f"[BgUpload] Starting background upload for {r2_key} (from {temp_path.name}), attempt {attempt}/{max_retries}...")
                upload_file_to_r2_sync(str(temp_path), r2_key, content_type, settings)
                logger.info(f"[BgUpload] Successfully uploaded {r2_key} in {time.time() - start_t:.2f}s")
                break
            except Exception as e:
                logger.warning(f"[BgUpload] Upload failed on attempt {attempt}/{max_retries} for {r2_key}: {e}")
                if attempt < max_retries:
                    time.sleep(delay)
                    delay *= 2
                else:
                    logger.error(f"[BgUpload] CRITICAL: Background upload permanently failed for {r2_key} after {max_retries} attempts.")
    finally:
        try:
            if temp_path.exists():
                temp_path.unlink()
                logger.debug(f"[BgUpload] Cleaned up temporary upload file {temp_path.name}")
        except Exception as e:
            logger.warning(f"[BgUpload] Failed to delete temporary file {temp_path}: {e}")

def _bg_upload_bytes_task(file_bytes: bytes, r2_key: str, content_type: str, settings: BaseWorkerSettings):
    """
    在后台线程中同步上传内存字节数据（带指数退避的应用层重试）。
    """
    max_retries = 3
    delay = 1.0
    for attempt in range(1, max_retries + 1):
        try:
            start_t = time.time()
            logger.info(f"[BgUpload] Starting background upload for bytes to {r2_key}, attempt {attempt}/{max_retries}...")
            s3 = _get_s3_client(settings)
            s3.put_object(
                Bucket=settings.R2_BUCKET,
                Key=r2_key,
                Body=file_bytes,
                ContentType=content_type
            )
            logger.info(f"[BgUpload] Successfully uploaded bytes to {r2_key} in {time.time() - start_t:.2f}s")
            break
        except Exception as e:
            logger.warning(f"[BgUpload] Bytes upload failed on attempt {attempt}/{max_retries} for {r2_key}: {e}")
            if attempt < max_retries:
                time.sleep(delay)
                delay *= 2
            else:
                logger.error(f"[BgUpload] CRITICAL: Background bytes upload permanently failed for {r2_key} after {max_retries} attempts.")

def upload_large_file_in_background(local_path: str, r2_key: str, content_type: str, settings: BaseWorkerSettings):
    """
    后台静默上传原图大文件或视频，先同步复制到安全临时路径，然后丢入线程池异步处理，不阻塞主任务完成汇报。
    """
    if not settings.R2_BUCKET:
        logger.warning(f"R2 Bucket not configured, skipping background upload for {r2_key}")
        return

    try:
        temp_path = _get_safe_bg_upload_path(local_path)
        shutil.copy2(local_path, str(temp_path))
        logger.debug(f"[BgUpload] Copied {local_path} to safe background temp: {temp_path}")
        _upload_executor.submit(_bg_upload_file_task, temp_path, r2_key, content_type, settings)
    except Exception as e:
        logger.error(f"[BgUpload] Failed to submit background upload task: {e}")

async def upload_to_r2(task_id: str, file_bytes: bytes, settings: BaseWorkerSettings) -> tuple[str, str, str]:
    """
    Upload to Cloudflare R2 using thread pool to avoid blocking asyncio event loop.
    Returns (public_url, thumb_url, thumb_r2_key).
    (Legacy memory upload for backward compatibility)
    """
    if not settings.R2_BUCKET:
        logger.warning(f"[Task {task_id}] R2 Bucket not configured, skipping actual upload.")
        await asyncio.sleep(0.5)
        return f"https://mock-r2.aigc.com/results/{task_id}.png", "", ""
        
    logger.info(f"[Task {task_id}] Uploading to Cloudflare R2 (FastUpload Optimization)...")
    
    object_name = f"aigc-tasks/{time.strftime('%Y/%m/%d')}/{task_id}.png"
    thumb_object_name = f"thumbs/{object_name}.webp"
    
    url = build_r2_object_url(object_name, settings)
    thumb_url = ""
    
    def _do_thumbnail_upload():
        nonlocal thumb_url
        try:
            thumb_bytes = generate_thumbnail(file_bytes=file_bytes)
            s3 = _get_s3_client(settings)
            s3.put_object(
                Bucket=settings.R2_BUCKET,
                Key=thumb_object_name,
                Body=thumb_bytes,
                ContentType='image/webp'
            )
            thumb_url = build_r2_object_url(thumb_object_name, settings)
            logger.info(f"[Task {task_id}] ⚡ [FastUpload] Thumbnail uploaded successfully: {thumb_url}")
        except Exception as e:
            logger.warning(f"[Task {task_id}] [FastUpload] Failed to upload thumbnail: {e}")
            
    try:
        await asyncio.to_thread(_do_thumbnail_upload)
    except Exception as e:
        logger.warning(f"[Task {task_id}] Thumbnail task exception: {e}")

    # 将大图大文件上传丢入后台线程池异步处理，不阻塞主任务完成汇报
    _upload_executor.submit(_bg_upload_bytes_task, file_bytes, object_name, 'image/png', settings)
    
    return url, thumb_url, thumb_object_name

def upload_file_to_r2_sync(local_path: str, r2_key: str, content_type: str, settings: BaseWorkerSettings) -> str:
    """
    Upload a local file directly to Cloudflare R2 synchronously with a specific object key.
    Returns the public URL of the uploaded object.
    """
    if not settings.R2_BUCKET:
        logger.warning(f"R2 Bucket not configured, skipping actual upload for {local_path}.")
        return f"https://mock-r2.aigc.com/{r2_key}"
        
    s3 = _get_s3_client(settings)
    
    extra_args = {}
    if content_type:
        extra_args['ContentType'] = content_type

    s3.upload_file(
        Filename=str(local_path),
        Bucket=settings.R2_BUCKET,
        Key=r2_key,
        ExtraArgs=extra_args
    )
    
    return build_r2_object_url(r2_key, settings)

def download_from_r2_sync(object_key: str, target_path: str, settings: BaseWorkerSettings):
    """
    Download a file from Cloudflare R2 synchronously.
    """
    if not settings.R2_BUCKET:
        raise ValueError("R2 Bucket not configured")
        
    logger.info(f"Downloading {object_key} from R2 to {target_path}...")
    
    s3 = _get_s3_client(settings)
    
    s3.download_file(
        Bucket=settings.R2_BUCKET,
        Key=object_key,
        Filename=str(target_path)
    )

def generate_thumbnail(file_bytes: bytes = None, file_path: str = None, is_video: bool = False, max_size: int = 512) -> bytes:
    """Generate a WebP thumbnail from image bytes, image file, or video file."""
    if is_video and file_path:
        # Extract first frame using ffmpeg
        try:
            out = subprocess.check_output([
                'ffmpeg', '-i', file_path, '-vframes', '1',
                '-f', 'image2pipe', '-vcodec', 'png', '-'
            ], stderr=subprocess.DEVNULL)
            img = Image.open(io.BytesIO(out))
        except Exception as e:
            raise RuntimeError(f"Failed to extract video frame: {e}")
    else:
        if file_bytes:
            img = Image.open(io.BytesIO(file_bytes))
        elif file_path:
            img = Image.open(file_path)
        else:
            raise ValueError("Must provide file_bytes or file_path")
            
    if img.mode in ('RGBA', 'P'):
        img = img.convert('RGB')
        
    img.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)
    
    out_buf = io.BytesIO()
    img.save(out_buf, format='WEBP', quality=80)
    return out_buf.getvalue()

def upload_with_thumbnail_sync(local_path: str, r2_key: str, content_type: str, settings: BaseWorkerSettings) -> tuple[str, str, str]:
    """
    Upload a local file directly to Cloudflare R2 synchronously and automatically generate 
    and upload a thumbnail (WebP format).
    极速优化版 (同步优先上传缩略图，异步静默后台上传原图/视频)
    Returns (public_url, thumb_url, thumb_r2_key).
    """
    public_url = build_r2_object_url(r2_key, settings)
    
    thumb_r2_key = f"thumbs/{r2_key}.webp"
    thumb_url = ""
    try:
        is_video = local_path.lower().endswith(('.mp4', '.avi', '.mov', '.webm'))
        thumb_bytes = generate_thumbnail(file_path=local_path, is_video=is_video)
        
        s3 = _get_s3_client(settings)
        s3.put_object(
            Bucket=settings.R2_BUCKET,
            Key=thumb_r2_key,
            Body=thumb_bytes,
            ContentType='image/webp'
        )
        thumb_url = build_r2_object_url(thumb_r2_key, settings)
        logger.info(f"⚡ [FastUpload] Thumbnail uploaded successfully to {thumb_url}")
    except Exception as e:
        logger.warning(f"[FastUpload] Failed to generate/upload thumbnail for {local_path}: {e}")
        thumb_r2_key = ""
        
    # 2. 将大文件上传丢入后台线程池异步处理，不阻塞主任务完成汇报！
    upload_large_file_in_background(local_path, r2_key, content_type, settings)
        
    return public_url, thumb_url, thumb_r2_key

