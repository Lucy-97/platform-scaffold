"""
Sub-Agent 并行委派执行器 — SubagentExecutor
=============================================

借鉴 DeerFlow 2.0 的 SubagentExecutor 设计，提供子任务并行执行能力。

核心功能：
  1. 将复杂任务拆分为多个子任务
  2. 使用 asyncio 并发执行多个子任务（可配置并发度）
  3. 每个子任务独立超时控制
  4. 子任务进度通过回调推送（可对接 SSE 事件流）
  5. 结果汇总和错误隔离

与 DeerFlow 的区别：
  - DeerFlow 使用 ThreadPoolExecutor + LangGraph SDK
  - AgentCore 使用纯 asyncio + LiteLLM，无额外线程池开销
  - AgentCore 子任务结果通过回调而非 StateGraph 之间传递

使用场景：
  - Agent 需要同时搜索多个关键词
  - 同时处理多个文件的代码审查
  - 并行调用多个工具收集信息
"""

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

from loguru import logger


class SubtaskStatus(str, Enum):
    """子任务状态枚举。"""
    PENDING = "pending"       # 等待执行
    RUNNING = "running"       # 正在执行
    COMPLETED = "completed"   # 执行完成
    FAILED = "failed"         # 执行失败
    TIMEOUT = "timeout"       # 执行超时
    CANCELLED = "cancelled"   # 被取消


@dataclass
class Subtask:
    """子任务定义。

    Attributes:
        task_id: 子任务唯一 ID。
        name: 子任务名称（人类可读）。
        instruction: 子任务指令（传递给子 Agent 的 prompt）。
        status: 当前状态。
        result: 执行结果（成功时为内容字符串）。
        error: 错误信息（失败时填充）。
        started_at: 开始执行时间。
        completed_at: 完成时间。
        metadata: 子任务附加数据（如使用的工具、token 消耗等）。
    """
    task_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    name: str = ""
    instruction: str = ""
    status: SubtaskStatus = SubtaskStatus.PENDING
    result: Optional[str] = None
    error: Optional[str] = None
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


# 子任务执行器回调类型
# 签名: async def(instruction: str, context: Dict) -> str
SubtaskHandler = Callable[..., Any]

# 进度回调类型
# 签名: async def(subtask: Subtask) -> None
ProgressCallback = Callable[..., Any]


class SubagentExecutor:
    """Sub-Agent 并行委派执行器。

    管理多个子任务的并行执行，提供并发控制、超时保护和进度推送。

    Args:
        max_concurrent: 最大并发子任务数。
        default_timeout: 默认单任务超时（秒）。
        handler: 子任务执行函数（接收指令，返回结果文本）。
        progress_callback: 子任务状态变更时的回调。

    Usage::

        async def my_handler(instruction, context):
            # 调用 LLM 或工具执行子任务
            return await run_agent_for_subtask(instruction)

        executor = SubagentExecutor(
            max_concurrent=3,
            handler=my_handler,
        )

        subtasks = [
            Subtask(name="搜索A", instruction="搜索关键词A"),
            Subtask(name="搜索B", instruction="搜索关键词B"),
        ]

        results = await executor.execute(subtasks)
    """

    def __init__(
        self,
        max_concurrent: int = 3,
        default_timeout: float = 300.0,
        handler: Optional[SubtaskHandler] = None,
        progress_callback: Optional[ProgressCallback] = None,
    ):
        self.max_concurrent = max_concurrent
        self.default_timeout = default_timeout
        self.handler = handler
        self.progress_callback = progress_callback
        # 并发控制信号量
        self._semaphore = asyncio.Semaphore(max_concurrent)

    async def execute(
        self,
        subtasks: List[Subtask],
        context: Optional[Dict[str, Any]] = None,
        timeout: Optional[float] = None,
    ) -> List[Subtask]:
        """并行执行多个子任务。

        使用 asyncio.gather 并发执行，信号量控制最大并发度。

        Args:
            subtasks: 子任务列表。
            context: 共享上下文（传递给 handler）。
            timeout: 全局超时（覆盖 default_timeout）。

        Returns:
            执行完毕的 Subtask 列表（含结果/错误/状态）。
        """
        if not self.handler:
            raise ValueError("SubagentExecutor: handler not configured")

        if not subtasks:
            return []

        task_timeout = timeout or self.default_timeout
        ctx = context or {}

        logger.info(
            f"[SubagentExecutor] Executing {len(subtasks)} subtasks "
            f"(max_concurrent={self.max_concurrent}, "
            f"timeout={task_timeout}s)"
        )

        # 并发执行所有子任务
        coroutines = [
            self._execute_one(subtask, ctx, task_timeout)
            for subtask in subtasks
        ]

        await asyncio.gather(*coroutines, return_exceptions=True)

        # 统计结果
        completed = sum(1 for s in subtasks if s.status == SubtaskStatus.COMPLETED)
        failed = sum(1 for s in subtasks if s.status in (SubtaskStatus.FAILED, SubtaskStatus.TIMEOUT))

        logger.info(
            f"[SubagentExecutor] Done: {completed} completed, "
            f"{failed} failed/timeout out of {len(subtasks)}"
        )

        return subtasks

    async def _execute_one(
        self,
        subtask: Subtask,
        context: Dict[str, Any],
        timeout: float,
    ) -> None:
        """执行单个子任务（受信号量控制）。

        Args:
            subtask: 子任务实例。
            context: 共享上下文。
            timeout: 超时时间。
        """
        async with self._semaphore:
            subtask.status = SubtaskStatus.RUNNING
            subtask.started_at = time.time()

            await self._notify_progress(subtask)

            try:
                # 带超时执行
                result = await asyncio.wait_for(
                    self.handler(subtask.instruction, context),
                    timeout=timeout,
                )
                subtask.status = SubtaskStatus.COMPLETED
                subtask.result = str(result)

            except asyncio.TimeoutError:
                subtask.status = SubtaskStatus.TIMEOUT
                subtask.error = f"Subtask timed out after {timeout}s"
                logger.warning(
                    f"[SubagentExecutor] Timeout: {subtask.name} "
                    f"(id={subtask.task_id})"
                )

            except asyncio.CancelledError:
                subtask.status = SubtaskStatus.CANCELLED
                subtask.error = "Subtask was cancelled"

            except Exception as e:
                subtask.status = SubtaskStatus.FAILED
                subtask.error = str(e)
                logger.error(
                    f"[SubagentExecutor] Failed: {subtask.name} "
                    f"(id={subtask.task_id}): {e}"
                )

            finally:
                subtask.completed_at = time.time()
                await self._notify_progress(subtask)

    async def _notify_progress(self, subtask: Subtask) -> None:
        """推送子任务进度到回调。

        Args:
            subtask: 状态变更的子任务。
        """
        if not self.progress_callback:
            return

        try:
            await self.progress_callback(subtask)
        except Exception as e:
            logger.error(
                f"[SubagentExecutor] Progress callback error: {e}"
            )
