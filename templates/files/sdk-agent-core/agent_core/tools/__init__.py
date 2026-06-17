"""
微内核工具框架 — agent_core.tools
==================================

取代传统 @tool 装饰器的扁平注册模式，让每个工具在注册时
即携带安全 / 并发 / UI 元数据，引擎在执行前自动走完拦截管线。

核心组件:
  - ``AgentCoreRobustTool``  — 微内核工具抽象基类（每个工具是一个"驱动"）
  - ``@agent_tool``      — 装饰器语法糖（将普通 async 函数一键升级为微内核工具）
  - ``ToolRegistry``    — 微内核工具注册中心
  - ``ToolExecutor``    — 工具执行管线（安全拦截 → UI 推送 → 并发调度 → 执行）

使用示例::

    from agent_core.tools import agent_tool, ToolSafetyLevel, ToolRegistry

    @agent_tool(
        name="bash",
        description="在安全沙箱中执行 bash 命令",
        parameters={...},
        safety_level=ToolSafetyLevel.MODERATE,
        is_destructive=lambda args: "rm -rf" in args.get("command", ""),
        concurrency_safe=False,
    )
    async def handle_bash(args: dict, ctx: dict) -> str:
        ...

    # 注册表自动收集被装饰的工具
    registry = ToolRegistry()
    registry.register(handle_bash)
"""

from agent_core.tools.base import (
    AgentCoreRobustTool,
    ToolExecutionResult,
    ToolSafetyLevel,
)
from agent_core.tools.decorator import agent_tool
from agent_core.tools.executor import ToolExecutor
from agent_core.tools.registry import ToolRegistry

__all__ = [
    "AgentCoreRobustTool",
    "ToolSafetyLevel",
    "ToolExecutionResult",
    "agent_tool",
    "ToolRegistry",
    "ToolExecutor",
]
