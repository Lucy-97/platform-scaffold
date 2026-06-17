import io
import subprocess
import tempfile
import os
from PIL import Image
# 放宽 Pillow 默认的图像像素限制（从 1.78 亿像素合理放宽至 3 亿像素以安全容纳极限大图超分，同时防御 DOS OOM 攻击）
Image.MAX_IMAGE_PIXELS = 300000000

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
            
    # Convert to RGB if needed (e.g., for RGBA to WebP)
    if img.mode in ('RGBA', 'P'):
        img = img.convert('RGB')
        
    img.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)
    
    out_buf = io.BytesIO()
    img.save(out_buf, format='WEBP', quality=80)
    return out_buf.getvalue()
