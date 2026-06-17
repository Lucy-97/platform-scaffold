"""
Handoff 协议 — Agent 间委派与结果汇报
========================================

定义 Agent 之间传递任务和结果的标准数据结构。

核心概念:
  - HandoffRequest: Supervisor 下发给 Worker 的任务描述
  - HandoffResult: Worker 执行完成后返回的结果
  - HandoffStatus: 委派状态枚举

设计参考:
  - Anthropic Agent SDK 的 handoff() 机制
  - A2A (Agent-to-Agent) Protocol 的 Message 结构
"""

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional


class HandoffStatus(str, Enum):
    """Handoff 状态枚举。"""
    PENDING = "pending"         # 等待执行
    RUNNING = "running"         # 正在执行
    COMPLETED = "completed"     # 执行成功
    FAILED = "failed"           # 执行失败
    TIMEOUT = "timeout"         # 执行超时


@dataclass
class HandoffRequest:
    """Supervisor → Worker 的任务委派请求。

    Attributes:
        id: 唯一请求 ID。
        from_agent: 发起方 Agent 名称。
        to_agent: 目标 Worker Agent 名称。
        task: 任务描述（自然语言指令）。
        context: 共享上下文（如前置 Agent 的输出、全局信息等）。
        priority: 优先级（0 最低，10 最高）。
        timeout: 超时时间（秒）。
        created_at: 创建时间戳。
    """
    from_agent: str
    to_agent: str
    task: str
    context: Dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    priority: int = 5
    timeout: float = 120.0
    created_at: float = field(default_factory=time.time)


@dataclass
class HandoffResult:
    """Worker → Supervisor 的结果汇报。

    Attributes:
        request_id: 对应的 HandoffRequest ID。
        agent: 执行 Worker 的名称。
        status: 执行状态。
        content: 执行结果内容（成功时）。
        error: 错误信息（失败时）。
        usage: Token 消耗统计。
        duration: 执行耗时（秒）。
        metadata: 附加元数据。
    """
    request_id: str
    agent: str
    status: HandoffStatus = HandoffStatus.COMPLETED
    content: str = ""
    error: str = ""
    usage: Dict[str, int] = field(default_factory=dict)
    duration: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def success(self) -> bool:
        """是否执行成功。"""
        return self.status == HandoffStatus.COMPLETED
