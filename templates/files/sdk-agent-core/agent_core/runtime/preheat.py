"""
资源预热调度器 — PreheatScheduler
===================================

利用 LLM 生成时间差（10-15s）并行预加载工具所需资源。

设计理念：
  用网络/生成延迟的时间差来掩盖计算延迟。
  当 StreamingToolSniffer 检测到某工具的关键参数时，
  立即调度对应的预热任务（如加载 ComfyUI 模型到 GPU）。

  优化前：[LLM 10s] → [加载模型 3s] → [渲染 10s] = 23s
  优化后：[LLM 10s + 并行加载 3s] → [渲染 10s]    = 20s
"""

import asyncio
import json
from typing import Any, Awaitable, Callable, Dict, Optional, Set

from loguru import logger


# 预热函数类型：接收工具名和参数，返回任意结果
PreheatFunction = Callable[[str, Dict[str, Any]], Awaitable[Any]]


class PreheatScheduler:
    """资源预热调度器——后台并行预加载工具所需资源。

    Args:
        preheat_handler: 默认预热处理函数。
            未注册专用处理函数的工具使用此默认值。
    """

    def __init__(
        self,
        preheat_handler: Optional[PreheatFunction] = None,
    ) -> None:
        self._default_handler = preheat_handler
        # 工具名 → 专用预热函数
        self._handlers: Dict[str, PreheatFunction] = {}
        # 已调度的任务 key → asyncio.Task
        self._tasks: Dict[str, asyncio.Task] = {}

    def register_handler(
        self,
        tool_name: str,
        handler: PreheatFunction,
    ) -> None:
        """为指定工具注册专用预热函数。

        Args:
            tool_name: 工具名称。
            handler: 预热处理函数。
        """
        self._handlers[tool_name] = handler
        logger.debug(f"[Preheat] 注册预热处理器: {tool_name}")

    async def schedule(
        self,
        tool_name: str,
        args: Dict[str, Any],
    ) -> bool:
        """调度预热任务（后台执行，不阻塞主流程）。

        同一工具+参数组合不会重复调度。

        Args:
            tool_name: 工具名称。
            args: 嗅探到的关键参数。

        Returns:
            True 表示成功调度，False 表示已存在或无处理器。
        """
        # 生成去重 key
        key = f"{tool_name}:{json.dumps(args, sort_keys=True)}"
        if key in self._tasks:
            return False  # 已调度，跳过

        # 查找处理器
        handler = self._handlers.get(tool_name, self._default_handler)
        if not handler:
            return False  # 无处理器，跳过

        # 后台启动
        task = asyncio.create_task(
            self._run_preheat(key, handler, tool_name, args)
        )
        self._tasks[key] = task
        logger.info(f"[Preheat] 🚀 调度预热: {tool_name} args={args}")
        return True

    async def _run_preheat(
        self,
        key: str,
        handler: PreheatFunction,
        tool_name: str,
        args: Dict[str, Any],
    ) -> None:
        """执行预热任务（内部使用）。"""
        try:
            await handler(tool_name, args)
            logger.info(f"[Preheat] ✅ 预热完成: {tool_name}")
        except Exception as e:
            # 预热失败不影响主流程——仅记录
            logger.warning(f"[Preheat] ⚠️ 预热失败: {tool_name} error={e}")
        finally:
            self._tasks.pop(key, None)

    async def cancel_all(self) -> None:
        """取消所有进行中的预热任务。"""
        for key, task in list(self._tasks.items()):
            if not task.done():
                task.cancel()
        self._tasks.clear()

    @property
    def active_count(self) -> int:
        """当前进行中的预热任务数。"""
        return sum(1 for t in self._tasks.values() if not t.done())
