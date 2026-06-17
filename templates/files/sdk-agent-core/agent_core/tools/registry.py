"""
微内核工具注册中心 — ToolRegistry
==================================

管理所有 AgentCoreRobustTool 实例，替代现有的全局 _TOOL_HANDLERS / _TOOL_DEFINITIONS。

功能:
  - register(tool)          — 注册微内核工具实例
  - register_legacy(...)    — 兼容旧式扁平注册（name, desc, schema, handler）
  - get(name)               — 按名称查找工具
  - get_all_definitions()   — 生成 OpenAI tools[] JSON（喂给 LLM）
"""

import asyncio
from typing import Any, Callable, Coroutine, Dict, List, Optional, Union

from loguru import logger

from agent_core.tools.base import AgentCoreRobustTool, ToolSafetyLevel


# 旧式 handler 签名类型
LegacyToolHandler = Callable[..., Union[str, Coroutine[Any, Any, str]]]


class _LegacyWrappedTool(AgentCoreRobustTool):
    """旧式裸函数包装为微内核工具 — 向后兼容用。

    所有安全/并发/UI 元数据均为默认值（MODERATE / concurrency_safe=True），
    行为与改造前完全一致：无拦截、无审批。
    """

    def __init__(
        self,
        name: str,
        description: str,
        parameters: Dict[str, Any],
        handler: LegacyToolHandler,
    ) -> None:
        self.name = name
        self.description = description
        self.parameters = parameters
        self._handler = handler
        # 旧式工具默认 MODERATE + 并发安全（与改造前行为一致）
        self.safety_level = ToolSafetyLevel.MODERATE
        self.concurrency_safe = True

    async def call(self, args: Dict[str, Any], ctx: Dict[str, Any]) -> str:
        """调用旧式 handler — 兼容 (args) 和 (args, ctx) 两种签名。"""
        import inspect

        sig = inspect.signature(self._handler)
        param_count = len(sig.parameters)

        # 旧式 handler 可能只接收 args 一个参数
        if param_count <= 1:
            result = self._handler(args)
        else:
            result = self._handler(args, ctx)

        # 兼容同步和异步
        if asyncio.iscoroutine(result) or asyncio.isfuture(result):
            result = await result
        return result if isinstance(result, str) else str(result)


class ToolRegistry:
    """微内核工具注册中心。

    管理所有 AgentCoreRobustTool 实例，提供统一的查找和定义导出接口。
    支持新旧两种注册方式，实现渐进迁移。
    """

    def __init__(self) -> None:
        self._tools: Dict[str, AgentCoreRobustTool] = {}

    def register(self, tool: AgentCoreRobustTool) -> None:
        """注册微内核工具实例。

        Args:
            tool: AgentCoreRobustTool 实例（通常由 @agent_tool 装饰器生成）。

        Raises:
            ValueError: 工具名称已存在时抛出。
        """
        if tool.name in self._tools:
            raise ValueError(
                f"工具名称 '{tool.name}' 已注册，不允许重复注册。"
                f"已注册: {self._tools[tool.name]!r}"
            )
        self._tools[tool.name] = tool
        logger.debug(f"[ToolRegistry] ✅ 注册微内核工具: {tool!r}")

    def register_legacy(
        self,
        name: str,
        description: str,
        parameters: Dict[str, Any],
        handler: LegacyToolHandler,
    ) -> None:
        """兼容旧式扁平注册 — 将裸函数包装为微内核工具。

        包装后的工具安全等级为 MODERATE，并发安全为 True，
        行为与改造前完全一致。

        Args:
            name: 工具名称。
            description: 工具描述。
            parameters: JSON Schema。
            handler: 旧式 handler 函数。
        """
        wrapped = _LegacyWrappedTool(
            name=name,
            description=description,
            parameters=parameters,
            handler=handler,
        )
        # 旧式注册允许覆盖（保持与现有 register_tool() 行为一致）
        self._tools[wrapped.name] = wrapped
        logger.debug(f"[ToolRegistry] 📦 注册旧式工具（已包装）: {wrapped.name!r}")

    def get(self, name: str) -> Optional[AgentCoreRobustTool]:
        """按名称查找已注册的工具。

        Args:
            name: 工具名称。

        Returns:
            AgentCoreRobustTool 实例，未找到时返回 None。
        """
        return self._tools.get(name)

    def get_all_definitions(self) -> List[Dict[str, Any]]:
        """生成所有已注册工具的 OpenAI tools[] JSON 定义。

        Returns:
            标准的 OpenAI Function-Calling 工具定义列表。
        """
        return [tool.to_openai_definition() for tool in self._tools.values()]

    def get_all_names(self) -> List[str]:
        """返回所有已注册工具名称。"""
        return list(self._tools.keys())

    def get_all_tools(self) -> List[AgentCoreRobustTool]:
        """返回所有已注册的工具实例列表。"""
        return list(self._tools.values())

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def __repr__(self) -> str:
        names = ", ".join(self._tools.keys())
        return f"<ToolRegistry tools=[{names}]>"
