"""
流式工具嗅探器 — StreamingToolSniffer
=======================================

在 LLM 流式输出过程中，从不完整的 JSON 片段中提前捕获工具信息。

工作原理（借鉴 Claude Code StreamingToolExecutor）：
  1. 监听 SSE 流中的 delta 片段
  2. 用正则从增量 JSON 中捕获工具名称和关键参数
  3. 一旦获取到工具名 → 立即触发权限预检
  4. 一旦获取到关键参数 → 立即触发资源预热
  5. 如检测到权限违规 → 通知引擎取消本次流

注意：
  - 嗅探是"尽力而为"的优化，不替代最终的完整 JSON 解析
  - 嗅探失败不影响正常流程
"""

import re
from enum import Enum
from typing import Any, Dict, Optional, Set

from loguru import logger


class SniffState(str, Enum):
    """流式嗅探状态机。"""
    WAITING = "waiting"           # 等待工具调用信号
    SNIFFING_NAME = "sniffing"    # 正在捕获工具名称
    CAPTURING_ARGS = "capturing"  # 正在捕获关键参数
    READY = "ready"               # 已获取足够信息可预热


class SniffResult:
    """嗅探结果。

    Attributes:
        action: 动作类型（"preheat" / "block" / None）。
        tool_name: 嗅探到的工具名称。
        args: 嗅探到的关键参数。
        reason: 阻断原因（仅 action="block" 时有值）。
    """
    __slots__ = ("action", "tool_name", "args", "reason")

    def __init__(
        self,
        action: str,
        tool_name: str = "",
        args: Optional[Dict[str, Any]] = None,
        reason: str = "",
    ):
        self.action = action
        self.tool_name = tool_name
        self.args = args or {}
        self.reason = reason


class StreamingToolSniffer:
    """流式工具嗅探器——在 LLM 输出过程中提前捕获工具信息。

    Args:
        known_tools: 已注册工具名称集合（用于存在性验证）。
        blocked_tools: 需要立即拦截的工具名称集合。
        preheat_patterns: 工具名 → 正则模式列表 的映射，
            用于从不完整 JSON 中提取可预热的参数。
    """

    def __init__(
        self,
        known_tools: Optional[Set[str]] = None,
        blocked_tools: Optional[Set[str]] = None,
        preheat_patterns: Optional[Dict[str, list]] = None,
    ):
        self._known_tools = known_tools or set()
        self._blocked_tools = blocked_tools or set()
        # 预热参数提取规则：工具名 → [(参数名, 正则模式)]
        self._preheat_patterns = preheat_patterns or {}
        self._buffer = ""
        self._state = SniffState.WAITING
        self._tool_name: Optional[str] = None

    def feed(self, chunk: str) -> Optional[SniffResult]:
        """喂入流式 chunk，返回嗅探结果。

        每次 LLM 流式输出 delta 时调用此方法。

        Args:
            chunk: LLM 输出的文本片段。

        Returns:
            - None: 尚未嗅探到有用信息
            - SniffResult(action="block"): 检测到高危工具，建议取消流
            - SniffResult(action="preheat"): 嗅探到工具+参数，可触发预热
        """
        self._buffer += chunk

        # 阶段一：尝试提取工具名称
        if self._state == SniffState.WAITING:
            name_match = re.search(
                r'"name"\s*:\s*"(\w+)"', self._buffer
            )
            if name_match:
                self._tool_name = name_match.group(1)
                self._state = SniffState.SNIFFING_NAME

                # ★ 立即预检：高危工具直接拦截
                if self._tool_name in self._blocked_tools:
                    return SniffResult(
                        action="block",
                        tool_name=self._tool_name,
                        reason=f"高危工具 {self._tool_name} 需要人工审批",
                    )

                # 未知工具警告（不阻断——可能是新注册的）
                if (
                    self._known_tools
                    and self._tool_name not in self._known_tools
                ):
                    logger.warning(
                        f"[Sniffer] 嗅探到未知工具: {self._tool_name}"
                    )

        # 阶段二：尝试提取可预热参数
        if (
            self._state == SniffState.SNIFFING_NAME
            and self._tool_name
            and self._tool_name in self._preheat_patterns
        ):
            patterns = self._preheat_patterns[self._tool_name]
            extracted = {}
            for param_name, regex in patterns:
                match = re.search(regex, self._buffer)
                if match:
                    extracted[param_name] = match.group(1)

            # 只要提取到至少一个参数就触发预热
            if extracted:
                self._state = SniffState.READY
                return SniffResult(
                    action="preheat",
                    tool_name=self._tool_name,
                    args=extracted,
                )

        return None

    def reset(self) -> None:
        """重置状态——每次工具调用完成后调用。"""
        self._buffer = ""
        self._state = SniffState.WAITING
        self._tool_name = None

    @property
    def current_tool_name(self) -> Optional[str]:
        """当前嗅探到的工具名称（可能为 None）。"""
        return self._tool_name
