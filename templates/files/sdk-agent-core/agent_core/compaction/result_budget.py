"""
大结果截断落盘 — apply_result_budget
========================================

工具执行后，如果返回结果超过指定字符数，
自动截断超出部分并落盘为临时文件，在结果中保留引用。

典型场景：
  - ComfyUI 返回 50K 渲染日志
  - ffprobe 返回大量元数据 JSON
  - 代码执行返回超长 stdout
"""

import hashlib
import json
import os
import time
from typing import Any, Dict, Optional

from loguru import logger


def apply_result_budget(
    tool_name: str,
    result: str,
    max_chars: int = 3000,
    dump_dir: Optional[str] = None,
) -> str:
    """对工具返回结果应用字符预算。

    如果 result 超过 max_chars：
      1. 保留头部 + 尾部作为上下文
      2. 中间部分落盘到临时文件
      3. 在结果中插入引用标记

    Args:
        tool_name: 工具名称。
        result: 原始工具返回。
        max_chars: 字符上限（默认 3000 ≈ 2K tokens）。
        dump_dir: 落盘目录（默认 /tmp/agent_result_dumps/）。

    Returns:
        截断后的结果（含引用标记）。
    """
    if len(result) <= max_chars:
        return result

    # 计算保留的头尾区间
    head_budget = int(max_chars * 0.6)  # 头部占 60%
    tail_budget = int(max_chars * 0.3)  # 尾部占 30%
    # 剩余 10% 给引用标记

    head = result[:head_budget]
    tail = result[-tail_budget:] if tail_budget > 0 else ""
    middle = result[head_budget : len(result) - tail_budget]

    # 落盘中间部分
    dump_path = _dump_to_file(tool_name, middle, dump_dir)

    # 构建截断结果
    truncated = (
        f"{head}\n\n"
        f"[截断] {tool_name} 输出过长 ({len(result)} 字符)，"
        f"中间 {len(middle)} 字符已保存至 {dump_path}\n\n"
        f"{tail}"
    )

    logger.info(
        f"[ResultBudget] {tool_name}: {len(result)} → {len(truncated)} 字符 "
        f"(截断 {len(middle)} 字符落盘)"
    )

    return truncated


def _dump_to_file(
    tool_name: str,
    content: str,
    dump_dir: Optional[str] = None,
) -> str:
    """将截断内容落盘到临时文件。"""
    base_dir = dump_dir or "/tmp/agent_result_dumps"
    os.makedirs(base_dir, exist_ok=True)

    # 用内容 hash 去重（相同内容不重复落盘）
    content_hash = hashlib.md5(content.encode()).hexdigest()[:8]
    timestamp = int(time.time())
    filename = f"{tool_name}_{timestamp}_{content_hash}.txt"
    filepath = os.path.join(base_dir, filename)

    if not os.path.exists(filepath):
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)

    return filepath
