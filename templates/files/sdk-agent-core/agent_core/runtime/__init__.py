"""
Runtime V2 — 解耦的 Agent 运行时引擎
======================================

将原 runtime.py 的同步阻塞循环重构为：
  - AsyncGenerator 事件流引擎（engine.py）
  - 结构化生命周期 Hook（hooks.py）
  - 流式工具嗅探与预热（streaming_sniffer.py / preheat.py）
  - 多智能体编排（supervisor.py + agent_node.py + handoff.py）
  - 事件流传输适配（transports/）
  - Token 成本追踪（cost_tracker.py）
  - 链路追踪（trace.py）

核心设计原则（借鉴 Claude Code QueryEngine）：
  - 引擎只 yield RuntimeEvent，不做任何 I/O 输出
  - 所有外围逻辑（审计/监控/压缩/UI推送）通过 Hook 注册
  - 消费端（Web/CLI/SDK）通过 Transport 适配各取所需
"""

from agent_core.runtime.events import RuntimeEvent, RuntimeEventType
from agent_core.runtime.hooks import HookPhase, LifecycleHookRegistry
from agent_core.runtime.engine import AgentRuntimeEngine
from agent_core.runtime.streaming_sniffer import StreamingToolSniffer
from agent_core.runtime.preheat import PreheatScheduler
from agent_core.runtime.agent_node import AgentNode
from agent_core.runtime.handoff import HandoffRequest, HandoffResult, HandoffStatus
from agent_core.runtime.supervisor import SupervisorAgent
from agent_core.runtime.cost_tracker import CostTracker, CostRecord
from agent_core.runtime.trace import TraceCollector, Trace, Span, SpanKind
from agent_core.runtime.safe_io import SafeWriter

__all__ = [
    # 事件系统
    "RuntimeEvent",
    "RuntimeEventType",
    # Hook 生命周期
    "HookPhase",
    "LifecycleHookRegistry",
    # 引擎
    "AgentRuntimeEngine",
    "StreamingToolSniffer",
    "PreheatScheduler",
    # 多 Agent 协调
    "AgentNode",
    "HandoffRequest",
    "HandoffResult",
    "HandoffStatus",
    "SupervisorAgent",
    # 可观测性
    "CostTracker",
    "CostRecord",
    "TraceCollector",
    "Trace",
    "Span",
    "Span",
    "SpanKind",
    # 鲁棒 I/O
    "SafeWriter",
]
