"""
运行时事件模型 — RuntimeEvent
==============================

引擎的唯一输出接口：所有状态变更、流式内容、工具进度、终态结果
均封装为结构化的 RuntimeEvent 通过 AsyncGenerator yield 给消费端。

设计意图：
  引擎本身不做任何 I/O（不 print、不 SSE、不 WebSocket）。
  消费端（Web/CLI/SDK）根据 event.type 各自适配输出方式。
"""

import time
from enum import Enum
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


class RuntimeEventType(str, Enum):
    """运行时事件类型枚举。

    按功能域分为四组：
      - 流式内容：LLM 输出的实时文本片段
      - 工具执行：工具的启动/进度/完成/失败
      - 状态变更：轮次边界、上下文压缩
      - 终态事件：最终结果/错误/中断
    """
    # ── 流式内容 ──
    STREAM_DELTA = "stream_delta"          # LLM 输出的流式文本片段
    STREAM_COMPLETE = "stream_complete"     # LLM 本轮流式输出结束

    # ── 工具执行 ──
    TOOL_START = "tool_start"              # 工具开始执行
    TOOL_PROGRESS = "tool_progress"        # 工具执行进度（如资源预热）
    TOOL_COMPLETE = "tool_complete"        # 工具执行完成
    TOOL_ERROR = "tool_error"              # 工具执行失败

    # ── 多 Agent 协调 ──
    AGENT_SPAWN = "agent_spawn"            # 子 Agent 被创建/调度
    AGENT_HANDOFF = "agent_handoff"        # 任务委派给 Worker
    AGENT_RESULT = "agent_result"          # Worker 返回结果
    AGENT_MERGE = "agent_merge"            # Supervisor 合并多 Worker 结果

    # ── 状态变更 ──
    TURN_START = "turn_start"              # 新一轮推理开始
    TURN_END = "turn_end"                  # 一轮推理结束
    COMPACT_TRIGGERED = "compact_triggered"  # 触发了上下文压缩

    # ── 终态 ──
    RESULT = "result"                      # 最终结果（文本或结构化数据）
    ERROR = "error"                        # 不可恢复的错误
    INTERRUPTED = "interrupted"            # 被外部中断
    TRACE_COMPLETE = "trace_complete"      # 链路追踪完成（携带完整 Span 树）



class RuntimeEvent(BaseModel):
    """运行时标准事件——引擎通过 yield 输出的唯一数据类型。

    Attributes:
        type: 事件类型。
        data: 事件载荷（文本片段 / 工具信息 / 错误详情等）。
        metadata: 额外元数据（如 token 用量、安全等级等）。
        turn: 当前轮次编号。
        timestamp: 事件产生的时间戳。
    """
    type: RuntimeEventType
    data: Any = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    turn: int = 0
    timestamp: float = Field(default_factory=time.time)

    def to_sse(self) -> str:
        """序列化为 SSE 格式字符串（Web 消费端使用）。"""
        import json
        payload = {
            "type": self.type.value,
            "data": self.data,
            "turn": self.turn,
        }
        if self.metadata:
            payload["metadata"] = self.metadata
        return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
