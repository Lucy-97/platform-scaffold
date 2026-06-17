"""
第一层：零成本正则清洗 — SnipCompactor
=========================================

纯字符串操作，毫秒级完成。剔除已过时的系统占位符、临时状态信息、
进度标记等"死文本"。

借鉴 Claude Code 的 snipCompactIfNeeded：
  Claude 用正则清洗已过时的 <system_status> 标签。
  AgentCore 场景中需要清洗的死文本包括：
    - [系统提示: 正在渲染/合成/生成中...] 临时进度
    - [ComfyUI 任务已提交] / [TTS 合成中] 等一次性通知
    - 工具执行的调试信息和 traceback 片段
"""

import re
from typing import Any, Dict, List, Tuple

from loguru import logger


# 需要清洗的正则模式列表（编译后缓存，避免重复编译开销）
_SNIP_PATTERNS = [
    # AgentCore 临时进度状态
    re.compile(r"\[系统提示[:：]\s*.*?(?:渲染|合成|生成|转码|上传).*?\]", re.DOTALL),
    # ComfyUI 一次性通知
    re.compile(r"\[ComfyUI\s+(?:任务|节点|WebSocket).*?\]"),
    # TTS/ffmpeg 临时状态
    re.compile(r"\[(?:TTS|ffmpeg|FFmpeg)\s+(?:合成|编码|转码).*?\]"),
    # 任务下发确认
    re.compile(r"\[(?:后台)?任务已(?:提交|下发|启动).*?\]"),
    # 进度百分比（已完成的旧进度没必要保留）
    re.compile(r"(?:进度|Progress)\s*[:：]?\s*\d+%\s*[:：]?\s*[\w\u4e00-\u9fff]+\.{0,3}"),
    # Agent 内部调试标记
    re.compile(r"\[DEBUG\].*?$", re.MULTILINE),
    # 连续空行压缩（>2个连续空行 → 1个）
    re.compile(r"\n{3,}"),
]

# 清洗后替换值
_SNIP_REPLACEMENTS = [
    "",  # 临时进度 → 删除
    "",  # ComfyUI 通知 → 删除
    "",  # TTS/ffmpeg → 删除
    "",  # 任务下发 → 删除
    "",  # 旧进度 → 删除
    "",  # DEBUG → 删除
    "\n\n",  # 多空行 → 双空行
]


class SnipCompactor:
    """零成本正则清洗器——剔除对话流中的死文本。

    每轮 LLM 调用前执行，无 LLM 开销，纯 CPU 正则。

    Args:
        extra_patterns: 额外的正则模式列表（业务方可扩展）。
    """

    def __init__(
        self,
        extra_patterns: List[Tuple[re.Pattern, str]] | None = None,
    ) -> None:
        # 合并内置 + 自定义模式
        self._patterns: List[Tuple[re.Pattern, str]] = list(
            zip(_SNIP_PATTERNS, _SNIP_REPLACEMENTS)
        )
        if extra_patterns:
            self._patterns.extend(extra_patterns)

    def compact(
        self, messages: List[Dict[str, Any]]
    ) -> Tuple[List[Dict[str, Any]], int]:
        """对消息列表执行正则清洗。

        仅清洗 role=tool 和 role=assistant 的 content 字段。
        system 和 user 消息不触碰（避免丢失重要指令）。

        Args:
            messages: 消息列表（原地修改）。

        Returns:
            (修改后的消息列表, 释放的字符数)。
        """
        total_freed = 0

        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content")

            # 仅清洗 tool 和 assistant 消息
            if role not in ("tool", "assistant") or not isinstance(content, str):
                continue

            original_len = len(content)
            cleaned = content

            for pattern, replacement in self._patterns:
                cleaned = pattern.sub(replacement, cleaned)

            # 去除首尾多余空白
            cleaned = cleaned.strip()

            freed = original_len - len(cleaned)
            if freed > 0:
                msg["content"] = cleaned
                total_freed += freed

        if total_freed > 0:
            logger.info(
                f"[SnipCompactor] 清洗释放 {total_freed} 字符 "
                f"(~{total_freed // 3} tokens)"
            )

        return messages, total_freed
