"""
AgentNode — 声明式 Agent 定义
===============================

每个 AgentNode 描述一个独立的智能体单元：
  - 有自己的名称、角色定位和 System Prompt
  - 可覆盖模型（默认继承 Supervisor 的模型）
  - 声明可用工具列表和可委派的下游 Agent

设计参考:
  - Anthropic Agent SDK 的 Agent 类
  - DeerFlow 的 Agent Node 定义
  - 与本模块 SupervisorAgent 配合使用
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class AgentNode:
    """声明式 Agent 节点。

    Attributes:
        name: Agent 唯一标识符（如 "researcher", "writer"）。
        role: 角色描述（人类可读，用于日志和 UI 展示）。
        system_prompt: 该 Agent 的 System Prompt。
        model: LLM 模型标识符（为空时继承编排器配置）。
        tools: 该 Agent 可用的工具名称列表。
        handoff_targets: 可委派任务的下游 Agent 名称列表。
        max_turns: 单次执行最大推理轮次，默认 5。
        temperature: LLM temperature，默认 0.7。
        metadata: 附加元数据（如限额、超时等自定义字段）。
    """
    name: str
    role: str = ""
    system_prompt: str = ""
    model: str = ""
    tools: List[str] = field(default_factory=list)
    handoff_targets: List[str] = field(default_factory=list)
    max_turns: int = 5
    temperature: float = 0.7
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("AgentNode.name 不能为空")
