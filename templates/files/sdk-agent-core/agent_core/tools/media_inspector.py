"""
结构化媒体检测工具 — MediaInspectorTool
==========================================

取代 Agent 频繁调用 bash ffprobe 的低效模式。

设计动机：
  Agent 需要知道视频/音频的时长、分辨率、编码等信息才能渲染分镜。
  以前是让 LLM 自己拼 ffprobe 命令然后解析输出（不稳定、费 Token）。
  现在提供结构化工具，输入文件路径，输出标准 JSON。
"""

import asyncio
import json
import os
from typing import Any, Dict, Optional

from loguru import logger

from agent_core.tools import ToolSafetyLevel, agent_tool


@agent_tool(
    name="inspect_media",
    description=(
        "检测媒体文件（视频/音频/图片）的元信息。"
        "返回时长、分辨率、编码格式、文件大小等结构化数据。"
        "支持 mp4/mov/mp3/wav/png/jpg 等常见格式。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "媒体文件路径（绝对路径或相对于项目目录）",
            },
            "extract_thumbnail": {
                "type": "boolean",
                "description": "是否提取缩略图（仅视频，默认 false）",
            },
        },
        "required": ["file_path"],
    },
    safety_level=ToolSafetyLevel.SAFE,
    concurrency_safe=True,
    ui_hook=lambda args: f"🎬 检测媒体: {os.path.basename(args.get('file_path', '?'))}",
)
async def inspect_media(args: dict, ctx: dict) -> str:
    """通过 ffprobe 检测媒体文件元信息并返回结构化 JSON。"""
    file_path = args.get("file_path", "")
    extract_thumb = args.get("extract_thumbnail", False)

    if not file_path:
        return json.dumps(
            {"status": "error", "message": "缺少 file_path 参数"},
            ensure_ascii=False,
        )

    if not os.path.isfile(file_path):
        return json.dumps(
            {"status": "error", "message": f"文件不存在: {file_path}"},
            ensure_ascii=False,
        )

    try:
        result = await _run_ffprobe(file_path)
        if extract_thumb:
            thumb_path = await _extract_thumbnail(file_path)
            if thumb_path:
                result["thumbnail"] = thumb_path

        result["status"] = "ok"
        return json.dumps(result, ensure_ascii=False)

    except Exception as e:
        logger.error(f"[MediaInspector] ffprobe 失败: {e}")
        return json.dumps(
            {"status": "error", "message": f"检测失败: {e}"},
            ensure_ascii=False,
        )


async def _run_ffprobe(file_path: str) -> Dict[str, Any]:
    """调用 ffprobe 获取结构化媒体信息。"""
    cmd = [
        "ffprobe",
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        file_path,
    ]

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()

    if proc.returncode != 0:
        error_msg = stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"ffprobe 退出码 {proc.returncode}: {error_msg}")

    raw = json.loads(stdout.decode("utf-8"))
    return _extract_key_info(raw, file_path)


def _extract_key_info(ffprobe_data: Dict, file_path: str) -> Dict[str, Any]:
    """从 ffprobe 原始输出中提取关键信息，输出简洁结构化 JSON。

    相比原始 ffprobe 输出（约 2000 字符），
    精炼后的 JSON（约 300 字符）可节约 ~85% Token。
    """
    fmt = ffprobe_data.get("format", {})
    streams = ffprobe_data.get("streams", [])

    result: Dict[str, Any] = {
        "file": os.path.basename(file_path),
        "size_mb": round(int(fmt.get("size", 0)) / 1048576, 2),
        "duration_sec": round(float(fmt.get("duration", 0)), 2),
        "format": fmt.get("format_name", "unknown"),
    }

    # 提取视频流信息
    for s in streams:
        if s.get("codec_type") == "video":
            result["video"] = {
                "codec": s.get("codec_name", ""),
                "width": s.get("width", 0),
                "height": s.get("height", 0),
                "fps": _parse_fps(s.get("avg_frame_rate", "0/1")),
                "bitrate_kbps": round(
                    int(s.get("bit_rate", 0)) / 1000
                ) if s.get("bit_rate") else None,
            }
        elif s.get("codec_type") == "audio":
            result["audio"] = {
                "codec": s.get("codec_name", ""),
                "sample_rate": int(s.get("sample_rate", 0)),
                "channels": s.get("channels", 0),
            }

    return result


def _parse_fps(fps_str: str) -> float:
    """解析 ffprobe 的帧率格式（如 '30000/1001' → 29.97）。"""
    try:
        parts = fps_str.split("/")
        if len(parts) == 2:
            num, den = int(parts[0]), int(parts[1])
            return round(num / max(den, 1), 2) if den else 0
        return float(fps_str)
    except (ValueError, ZeroDivisionError):
        return 0.0


async def _extract_thumbnail(
    file_path: str, output_dir: Optional[str] = None
) -> Optional[str]:
    """从视频文件提取缩略图。"""
    base = os.path.splitext(os.path.basename(file_path))[0]
    out_dir = output_dir or "/tmp/agent_thumbnails"
    os.makedirs(out_dir, exist_ok=True)
    thumb_path = os.path.join(out_dir, f"{base}_thumb.jpg")

    cmd = [
        "ffmpeg", "-y", "-i", file_path,
        "-ss", "00:00:01", "-vframes", "1",
        "-q:v", "5", thumb_path,
    ]

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await proc.communicate()

    return thumb_path if os.path.isfile(thumb_path) else None
