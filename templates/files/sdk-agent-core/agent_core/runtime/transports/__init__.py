"""
runtime/transports/ — 事件流传输适配层
=======================================

将 RuntimeEvent 事件流适配为不同的传输协议：
  - SSETransport:       HTTP Server-Sent Events
  - WebSocketTransport: WebSocket 双向通道
  - CLITransport:       终端渲染（调试用）

设计原则：
  引擎只 yield RuntimeEvent，Transport 负责序列化和推送。
"""

from agent_core.runtime.transports.base import TransportAdapter
from agent_core.runtime.transports.sse import SSETransport
from agent_core.runtime.transports.websocket import WebSocketTransport

__all__ = [
    "TransportAdapter",
    "SSETransport",
    "WebSocketTransport",
]
