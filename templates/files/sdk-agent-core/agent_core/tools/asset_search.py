"""
结构化素材搜索工具 — AssetSearchTool
=======================================

提供统一的素材搜索接口，支持按类型、标签、过滤条件搜索
项目中的媒体素材（图片/音频/视频/字体等）。

设计动机：
  Agent 需要知道项目中有哪些可用的角色立绘、背景图、BGM 等，
  以前是让 LLM 自己拼 ls/find 命令（不稳定）。
  现在提供结构化搜索工具，输入过滤条件，输出标准 JSON 列表。
"""

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger

from agent_core.tools import ToolSafetyLevel, agent_tool


# 素材类型 → 文件扩展名映射
_ASSET_TYPE_EXTENSIONS = {
    "image": {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".svg"},
    "video": {".mp4", ".mov", ".avi", ".mkv", ".webm"},
    "audio": {".mp3", ".wav", ".ogg", ".flac", ".aac", ".m4a"},
    "font": {".ttf", ".otf", ".woff", ".woff2"},
    "document": {".md", ".txt", ".pdf", ".docx"},
    "model_3d": {".obj", ".fbx", ".gltf", ".glb"},
}


@agent_tool(
    name="search_assets",
    description=(
        "搜索项目素材库中的媒体资源。"
        "支持按类型（image/video/audio/font）、关键词、"
        "文件大小等条件过滤。返回匹配的素材列表（路径+元信息）。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "asset_type": {
                "type": "string",
                "description": "素材类型: image / video / audio / font / document / all",
            },
            "keyword": {
                "type": "string",
                "description": "文件名关键词（支持模糊匹配）",
            },
            "directory": {
                "type": "string",
                "description": "搜索目录（默认项目根目录）",
            },
            "max_results": {
                "type": "integer",
                "description": "最多返回数量（默认 20）",
            },
        },
        "required": [],
    },
    safety_level=ToolSafetyLevel.SAFE,
    concurrency_safe=True,
    ui_hook=lambda args: (
        f"📂 搜索素材: type={args.get('asset_type', 'all')} "
        f"keyword={args.get('keyword', '*')}"
    ),
)
async def search_assets(args: dict, ctx: dict) -> str:
    """搜索项目素材并返回结构化列表。"""
    asset_type = args.get("asset_type", "all")
    keyword = args.get("keyword", "")
    directory = args.get("directory", "")
    max_results = args.get("max_results", 20)

    # 确定搜索目录
    project_dir = ctx.get("project_dir", "")
    search_dir = directory or project_dir

    if not search_dir or not os.path.isdir(search_dir):
        return json.dumps(
            {
                "status": "error",
                "message": f"搜索目录不存在: {search_dir or '(未指定)'}",
            },
            ensure_ascii=False,
        )

    # 确定目标扩展名集合
    if asset_type == "all":
        target_exts = set()
        for exts in _ASSET_TYPE_EXTENSIONS.values():
            target_exts.update(exts)
    else:
        target_exts = _ASSET_TYPE_EXTENSIONS.get(asset_type, set())
        if not target_exts:
            return json.dumps(
                {
                    "status": "error",
                    "message": (
                        f"未知素材类型: {asset_type}，"
                        f"支持: {list(_ASSET_TYPE_EXTENSIONS.keys())}"
                    ),
                },
                ensure_ascii=False,
            )

    # 搜索文件
    results = _scan_directory(
        search_dir, target_exts, keyword, max_results
    )

    return json.dumps(
        {
            "status": "ok",
            "count": len(results),
            "assets": results,
        },
        ensure_ascii=False,
    )


def _scan_directory(
    root: str,
    target_exts: set,
    keyword: str,
    max_results: int,
) -> List[Dict[str, Any]]:
    """递归扫描目录，匹配素材文件。"""
    results: List[Dict[str, Any]] = []
    keyword_lower = keyword.lower()

    for dirpath, _, filenames in os.walk(root):
        for filename in filenames:
            if len(results) >= max_results:
                return results

            ext = os.path.splitext(filename)[1].lower()
            if ext not in target_exts:
                continue

            if keyword_lower and keyword_lower not in filename.lower():
                continue

            full_path = os.path.join(dirpath, filename)
            try:
                stat = os.stat(full_path)
                # 推断素材类型
                file_type = "unknown"
                for type_name, exts in _ASSET_TYPE_EXTENSIONS.items():
                    if ext in exts:
                        file_type = type_name
                        break

                results.append({
                    "path": full_path,
                    "name": filename,
                    "type": file_type,
                    "size_kb": round(stat.st_size / 1024, 1),
                    "relative": os.path.relpath(full_path, root),
                })
            except OSError:
                continue

    return results
