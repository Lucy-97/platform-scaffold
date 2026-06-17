"""
@agent_tool 装饰器 — 将普通 async 函数一键升级为微内核工具
============================================================

核心设计：装饰器内部创建一个 AgentCoreRobustTool 子类实例，
将被装饰函数绑定为 call() 方法，并支持通过关键字参数
声明安全/并发/UI 元数据。

示例::

    @agent_tool(
        name="bash",
        description="在安全沙箱中执行 bash 命令",
        parameters={...},
        safety_level=ToolSafetyLevel.MODERATE,
        is_destructive=lambda args: "rm -rf" in args.get("command", ""),
        is_read_only=lambda args: bool(re.match(r"^\\s*(ls|cat)", args.get("command", ""))),
        concurrency_safe=False,
        ui_hook=lambda args: f"🖥️ 执行: `{args.get('command', '')[:50]}`",
    )
    async def handle_bash(args: dict, ctx: dict) -> str:
        ...
"""

import asyncio
import functools
from typing import Any, Callable, Dict, Optional, Union

from agent_core.tools.base import AgentCoreRobustTool, ToolSafetyLevel


# 安全回调类型：可以是 Callable[[dict], bool] 或固定 bool 值
SafetyCallback = Union[Callable[[Dict[str, Any]], bool], bool, None]
# UI 钩子类型：可以是 Callable[[dict], str] 或固定 str 值
UIHookCallback = Union[Callable[[Dict[str, Any]], str], str, None]


class _DecoratedTool(AgentCoreRobustTool):
    """由 @agent_tool 装饰器内部生成的工具实例。

    将被装饰的 async 函数作为 call() 方法绑定，
    并将装饰器参数映射为微内核骨架属性。
    """

    def __init__(
        self,
        *,
        name: str,
        description: str,
        parameters: Dict[str, Any],
        handler: Callable,
        safety_level: ToolSafetyLevel,
        is_destructive_cb: SafetyCallback,
        is_read_only_cb: SafetyCallback,
        concurrency_safe: bool,
        ui_hook_cb: UIHookCallback,
    ) -> None:
        self.name = name
        self.description = description
        self.parameters = parameters
        self._handler = handler
        self.safety_level = safety_level
        self._is_destructive_cb = is_destructive_cb
        self._is_read_only_cb = is_read_only_cb
        self.concurrency_safe = concurrency_safe
        self._ui_hook_cb = ui_hook_cb

    def is_read_only(self, input_data: Dict[str, Any]) -> bool:
        """支持三种声明方式：lambda/函数、固定 bool、默认回退。"""
        if callable(self._is_read_only_cb):
            return self._is_read_only_cb(input_data)
        if isinstance(self._is_read_only_cb, bool):
            return self._is_read_only_cb
        # 未指定 → 回退到基类默认行为（基于 safety_level）
        return super().is_read_only(input_data)

    def is_destructive(self, input_data: Dict[str, Any]) -> bool:
        """支持三种声明方式：lambda/函数、固定 bool、默认回退。"""
        if callable(self._is_destructive_cb):
            return self._is_destructive_cb(input_data)
        if isinstance(self._is_destructive_cb, bool):
            return self._is_destructive_cb
        # 未指定 → 回退到基类默认行为（基于 safety_level）
        return super().is_destructive(input_data)

    def get_activity_description(self, input_data: Dict[str, Any]) -> str:
        """支持两种声明方式：lambda/函数、固定字符串。"""
        if callable(self._ui_hook_cb):
            return self._ui_hook_cb(input_data)
        if isinstance(self._ui_hook_cb, str):
            return self._ui_hook_cb
        return super().get_activity_description(input_data)

    async def call(self, args: Dict[str, Any], ctx: Dict[str, Any]) -> str:
        """调用被装饰的原函数。支持同步和异步 handler。"""
        result = self._handler(args, ctx)
        # 兼容同步 handler
        if asyncio.iscoroutine(result) or asyncio.isfuture(result):
            result = await result
        return result


def agent_tool(
    *,
    name: str,
    description: str,
    parameters: Dict[str, Any],
    safety_level: ToolSafetyLevel = ToolSafetyLevel.MODERATE,
    is_destructive: SafetyCallback = None,
    is_read_only: SafetyCallback = None,
    concurrency_safe: bool = True,
    ui_hook: UIHookCallback = None,
) -> Callable:
    """微内核工具装饰器 — 将普通函数升级为 AgentCoreRobustTool 实例。

    装饰后，原函数被替换为一个 ``_DecoratedTool`` 实例，
    该实例同时实现了 ``AgentCoreRobustTool`` 的完整接口。

    Args:
        name: 工具唯一标识符（LLM tool_calls 中使用）。
        description: 一行描述（展示在 LLM tools schema 中）。
        parameters: OpenAI 兼容的 JSON Schema（定义输入参数）。
        safety_level: 静态安全等级默认值。
        is_destructive: 动态高危判定回调（lambda/函数/bool）。
        is_read_only: 动态只读判定回调（lambda/函数/bool）。
        concurrency_safe: 是否允许并发执行。
        ui_hook: 面向前端的活动描述回调（lambda/函数/str）。

    Returns:
        装饰器函数。

    使用示例::

        @agent_tool(name="read_file", description="读取文件", parameters={...},
                   safety_level=ToolSafetyLevel.SAFE)
        async def handle_read_file(args: dict, ctx: dict) -> str:
            ...
    """

    def decorator(fn: Callable) -> _DecoratedTool:
        tool = _DecoratedTool(
            name=name,
            description=description,
            parameters=parameters,
            handler=fn,
            safety_level=safety_level,
            is_destructive_cb=is_destructive,
            is_read_only_cb=is_read_only,
            concurrency_safe=concurrency_safe,
            ui_hook_cb=ui_hook,
        )
        # 保留原函数元信息（便于 inspect / 调试）
        functools.update_wrapper(tool, fn)
        return tool

    return decorator
