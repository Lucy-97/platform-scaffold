"""
第二层：工具返回脱水器 — ToolResultDehydrator
================================================

按 tool_use_id 分组匹配，将"已过期"的旧版工具返回替换为精简占位符。

核心策略：
  - KEEP_FOREVER 白名单（角色设定/世界观永远保留完整内容）
  - AGGRESSIVE_DEHYDRATE 名单（ComfyUI 日志/ffmpeg 等≥1轮即脱水）
  - 通用策略：超过 N 轮的工具返回一律脱水
"""

from typing import Any, Dict, List, Set

from loguru import logger


class ToolResultDehydrator:
    """工具返回脱水器——将过期的大体积工具返回替换为轻量占位符。

    设计决策：
      - 只保留最近 keep_turns 轮的完整工具返回，更早的一律脱水
      - 白名单工具永远保留完整内容
      - 激进脱水名单中的工具，超过 1 轮即脱水

    Args:
        keep_turns: 通用保留轮次（默认 2 轮）。
        keep_forever: 永远保留完整内容的工具名集合。
        aggressive_dehydrate: 激进脱水的工具名集合（1 轮即脱水）。
    """

    def __init__(
        self,
        keep_turns: int = 2,
        keep_forever: Set[str] | None = None,
        aggressive_dehydrate: Set[str] | None = None,
    ) -> None:
        self.keep_turns = keep_turns
        self.keep_forever = keep_forever or {
            "get_world_settings",
            "get_character_profiles",
            "vfs_read",  # VFS 读取的设定类内容也保留
        }
        self.aggressive_dehydrate = aggressive_dehydrate or {
            "comfyui_render",
            "run_ffmpeg",
            "search_assets",
            "exec_code",
            "bash",
        }

    def dehydrate(
        self,
        messages: List[Dict[str, Any]],
        current_turn: int,
    ) -> tuple[List[Dict[str, Any]], int]:
        """遍历消息列表，将过期的工具返回替换为占位符。

        需要消息上携带 _turn 和 _tool_name 元数据。
        如果没有这些元数据，则使用后备策略：
        按消息在列表中的位置估算轮次。

        Args:
            messages: 消息列表（原地修改）。
            current_turn: 当前对话轮次。

        Returns:
            (修改后的消息列表, 释放的字符数)。
        """
        total_freed = 0
        # 后备策略：按 assistant+tool 对出现的次数估算轮次
        estimated_turn = 0

        for i, msg in enumerate(messages):
            if msg.get("role") == "assistant" and msg.get("tool_calls"):
                estimated_turn += 1

            if msg.get("role") != "tool":
                continue

            content = msg.get("content", "")
            if not isinstance(content, str) or len(content) < 50:
                continue  # 太短的不值得脱水

            # 获取工具名和轮次信息
            tool_name = msg.get("_tool_name", "")
            msg_turn = msg.get("_turn", estimated_turn)
            turn_gap = current_turn - msg_turn

            # 白名单：永远保留
            if tool_name in self.keep_forever:
                continue

            # 保存原始内容（首次脱水时备份）
            if "_original_length" not in msg:
                msg["_original_length"] = len(content)

            original_length = msg["_original_length"]

            # 激进脱水：≥1 轮即脱水
            if tool_name in self.aggressive_dehydrate and turn_gap >= 1:
                placeholder = (
                    f"[已折叠] {tool_name} 在第 {msg_turn} 轮的输出"
                    f"（原始 {original_length} 字符）。"
                    f"如需重新查看，请再次调用该工具。"
                )
                freed = len(content) - len(placeholder)
                if freed > 0:
                    msg["content"] = placeholder
                    total_freed += freed

            # 通用策略：超过 keep_turns 轮的一律脱水
            elif turn_gap >= self.keep_turns:
                placeholder = (
                    f"[已折叠] {tool_name or '工具'} 历史输出"
                    f"（原始 {original_length} 字符）。"
                    f"如需详情请重新调用。"
                )
                freed = len(content) - len(placeholder)
                if freed > 0:
                    msg["content"] = placeholder
                    total_freed += freed

        if total_freed > 0:
            logger.info(
                f"[Microcompact] 脱水释放 {total_freed} 字符 "
                f"(~{total_freed // 3} tokens)"
            )

        return messages, total_freed
