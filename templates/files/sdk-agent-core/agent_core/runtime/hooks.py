"""
生命周期 Hook 注册表 — LifecycleHookRegistry
===============================================

统一替代原 MiddlewareChain，提供 7 个阶段的挂载点。

设计原则（借鉴 Claude Code 的 AOP 切面架构）：
  - Hook 是完全解耦的异步函数，不修改主循环代码
  - 支持优先级排序（priority 越小越先执行）
  - 某个 Hook 失败不影响主流程（catch + log）
  - 相比旧 MiddlewareChain 的 before/after 两个时机点，
    Hook 提供了 7 个细粒度阶段，覆盖工具级和错误级事件
"""

from enum import Enum
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from loguru import logger


# Hook 函数签名：接收关键字参数，返回可选值
HookFunction = Callable[..., Awaitable[Any]]


class HookPhase(str, Enum):
    """生命周期阶段——Hook 的 7 个挂载点。

    执行流程::

        PRE_SAMPLING → [LLM 调用] → POST_SAMPLING
                                         ↓
                              PRE_TOOL → [工具执行] → POST_TOOL
                                         ↓ (异常)
                                      ON_ERROR
                                         ↓ (压缩)
                                      ON_COMPACT
                                         ↓ (完成)
                                      ON_COMPLETE
    """
    PRE_SAMPLING = "pre_sampling"      # LLM 调用之前（压缩/注入/日志落盘）
    POST_SAMPLING = "post_sampling"    # LLM 响应之后（审计/用量统计）
    PRE_TOOL = "pre_tool"              # 单个工具执行之前（权限预检）
    POST_TOOL = "post_tool"            # 单个工具执行之后（结果截断/缓存）
    ON_ERROR = "on_error"              # 发生错误时（上报/资源清理）
    ON_COMPACT = "on_compact"          # 触发上下文压缩时
    ON_COMPLETE = "on_complete"        # 整个任务完成时（记忆沉淀/日志归档）


class _HookEntry:
    """内部用：带优先级的 Hook 条目。"""
    __slots__ = ("priority", "fn", "name")

    def __init__(self, priority: int, fn: HookFunction, name: str):
        self.priority = priority
        self.fn = fn
        self.name = name

    def __repr__(self) -> str:
        return f"<Hook {self.name} p={self.priority}>"


class LifecycleHookRegistry:
    """生命周期 Hook 注册表——引擎外围逻辑的统一挂载中心。

    使用示例::

        registry = LifecycleHookRegistry()

        async def token_monitor_hook(response=None, turn=0, **kw):
            print(f"Turn {turn}: {response.usage.total_tokens} tokens")

        registry.register(
            HookPhase.POST_SAMPLING,
            token_monitor_hook,
            name="token_monitor",
            priority=50,
        )
    """

    def __init__(self) -> None:
        self._hooks: Dict[HookPhase, List[_HookEntry]] = {
            phase: [] for phase in HookPhase
        }

    def register(
        self,
        phase: HookPhase,
        hook: HookFunction,
        name: Optional[str] = None,
        priority: int = 100,
    ) -> None:
        """注册 Hook 到指定阶段。

        Args:
            phase: 生命周期阶段。
            hook: 异步 Hook 函数。
            name: Hook 名称（用于日志，默认取函数名）。
            priority: 优先级（越小越先执行），默认 100。
        """
        hook_name = name or getattr(hook, "__name__", "anonymous")
        entry = _HookEntry(priority=priority, fn=hook, name=hook_name)
        self._hooks[phase].append(entry)
        # 按优先级排序
        self._hooks[phase].sort(key=lambda e: e.priority)
        logger.debug(
            f"[HookRegistry] 注册 Hook: {hook_name} → {phase.value} "
            f"(priority={priority})"
        )

    def unregister(self, phase: HookPhase, name: str) -> bool:
        """按名称移除 Hook。

        Args:
            phase: 生命周期阶段。
            name: Hook 名称。

        Returns:
            是否成功移除。
        """
        entries = self._hooks[phase]
        for i, entry in enumerate(entries):
            if entry.name == name:
                entries.pop(i)
                logger.debug(f"[HookRegistry] 移除 Hook: {name} from {phase.value}")
                return True
        return False

    async def execute(
        self, phase: HookPhase, **context: Any
    ) -> List[Any]:
        """执行指定阶段的所有 Hook。

        Hook 异常不阻塞主流程——仅记录错误日志后继续执行后续 Hook。

        Args:
            phase: 生命周期阶段。
            **context: 传递给 Hook 的上下文参数。

        Returns:
            各 Hook 的返回值列表（异常的返回 None）。
        """
        entries = self._hooks[phase]
        if not entries:
            return []

        results: List[Any] = []
        for entry in entries:
            try:
                result = await entry.fn(**context)
                results.append(result)
            except Exception as e:
                # ★ Hook 失败不阻塞主流程——这是核心设计决策
                logger.error(
                    f"[HookRegistry] Hook 执行失败: {entry.name} "
                    f"phase={phase.value} error={e}"
                )
                results.append(None)

        return results

    def get_hook_names(self, phase: HookPhase) -> List[str]:
        """获取指定阶段的所有 Hook 名称列表。"""
        return [e.name for e in self._hooks[phase]]

    def summary(self) -> Dict[str, List[str]]:
        """获取全部 Hook 注册摘要（用于调试/日志）。"""
        return {
            phase.value: [f"{e.name}(p={e.priority})" for e in entries]
            for phase, entries in self._hooks.items()
            if entries
        }
