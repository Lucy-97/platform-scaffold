"""
微内核工具抽象基类 — AgentCoreRobustTool
====================================

定义工具的完整"驱动骨架"：安全声明、并发标记、UI 钩子、执行入口。

设计理念（借鉴 Claude Code CLI 的 Tool.ts 抽象工厂）：
  - 工具不再是一个"函数指针"，而是一个拥有完整生命周期的"驱动程序"
  - is_read_only / is_destructive 为 **实例方法**，支持基于输入的动态判定
  - safety_level 为静态默认值，动态回调可在运行时覆盖升级
"""

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional


class ToolSafetyLevel(str, Enum):
    """工具安全等级 — 决定引擎的拦截策略。

    三级分层对标 Claude Code 的 isReadOnly / isDestructive 双标记：
      - SAFE:        只读操作（如 vfs_ls, search_web），自动放行
      - MODERATE:    有副作用但通常可逆（如 write_file），记录日志
      - DESTRUCTIVE: 不可逆高危操作（如 rm -rf, 覆盖剧本库），强制人类审批
    """
    SAFE = "safe"
    MODERATE = "moderate"
    DESTRUCTIVE = "destructive"


@dataclass
class ToolExecutionResult:
    """工具执行结果 — 封装业务返回值 + 管线元数据。

    Attributes:
        status: 执行状态 ("ok" / "error" / "denied" / "timeout")。
        content: 工具返回的业务数据字符串（塞回 LLM messages）。
        tool_name: 工具名称。
        duration_ms: 执行耗时（毫秒）。
        was_approved: 是否经过人类审批（仅 destructive 工具有值）。
        safety_level: 本次执行的安全等级。
    """
    status: str = "ok"
    content: str = ""
    tool_name: str = ""
    duration_ms: float = 0.0
    was_approved: Optional[bool] = None
    safety_level: ToolSafetyLevel = ToolSafetyLevel.MODERATE


class AgentCoreRobustTool(ABC):
    """微内核工具抽象基类 — 所有 AgentCore 工具的统一骨架。

    每个继承方必须实现 ``call()`` 方法（实际业务逻辑），
    并可选覆盖安全/并发/UI 相关方法来声明工具的元行为。

    Attributes:
        name: 工具唯一标识符（LLM 在 tool_calls 中使用）。
        description: 一行描述（展示在 LLM 的 tools schema 中）。
        parameters: OpenAI 兼容的 JSON Schema（定义输入参数格式）。
        safety_level: 静态安全等级默认值。
        concurrency_safe: 是否允许与其他工具并发执行。
    """

    name: str = ""
    description: str = ""
    parameters: Dict[str, Any] = {}
    safety_level: ToolSafetyLevel = ToolSafetyLevel.MODERATE
    concurrency_safe: bool = True

    # ---- 安全生命周期 ----

    def is_read_only(self, input_data: Dict[str, Any]) -> bool:
        """回调判定：当前入参是否为只读操作？

        默认行为：safety_level == SAFE 时返回 True。
        子类可覆盖此方法实现基于正则/规则的动态判定。

        Args:
            input_data: LLM 传入的工具参数 dict。

        Returns:
            True 表示只读安全，引擎将自动放行不弹审批。
        """
        return self.safety_level == ToolSafetyLevel.SAFE

    def is_destructive(self, input_data: Dict[str, Any]) -> bool:
        """回调判定：当前入参是否为高危破坏性操作？

        默认行为：safety_level == DESTRUCTIVE 时返回 True。
        子类可覆盖此方法实现动态判定（如正则匹配 "rm -rf"）。

        Args:
            input_data: LLM 传入的工具参数 dict。

        Returns:
            True 表示高危，引擎将强制挂起等待人类审批。
        """
        return self.safety_level == ToolSafetyLevel.DESTRUCTIVE

    # ---- 并发控制 ----

    def is_concurrency_safe(self) -> bool:
        """是否允许与其他工具并发执行？

        默认取 self.concurrency_safe 属性值。
        如果返回 False，引擎会在全局串行锁内执行此工具。

        Returns:
            True 表示可以安全并发。
        """
        return self.concurrency_safe

    # ---- 面向人类的 UI 钩子 ----

    def get_activity_description(self, input_data: Dict[str, Any]) -> str:
        """面向前端的活动描述 — 推送给 SSE/WebSocket 展示给用户。

        默认返回 "🔧 正在执行 {name}..."。
        子类可覆盖，返回更有业务语义的描述。

        Args:
            input_data: LLM 传入的工具参数 dict。

        Returns:
            对人类友好的活动描述字符串。
        """
        return f"🔧 正在执行 {self.name}..."

    # ---- 执行入口 ----

    @abstractmethod
    async def call(self, args: Dict[str, Any], ctx: Dict[str, Any]) -> str:
        """实际业务逻辑执行入口 — 子类必须实现。

        Args:
            args: LLM 传入的工具参数 dict。
            ctx: 运行时上下文（含 session_id, agent_id 等）。

        Returns:
            JSON 格式的结果字符串（塞回 LLM messages 的 tool result）。
        """
        ...

    # ---- 工具定义生成 ----

    def to_openai_definition(self) -> Dict[str, Any]:
        """生成 OpenAI Function-Calling 兼容的工具定义 dict。

        Returns:
            标准的 {"type": "function", "function": {...}} 结构。
        """
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def __repr__(self) -> str:
        return (
            f"<{self.__class__.__name__} name={self.name!r} "
            f"safety={self.safety_level.value} "
            f"concurrency_safe={self.concurrency_safe}>"
        )
