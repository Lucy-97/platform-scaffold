"""
IM 渠道集成 — channels 包
===========================

借鉴 DeerFlow 2.0 的 MessageBus 架构，为 AgentCore 平台提供
多平台即时通讯渠道集成能力。

架构概览::

    IM 平台 (飞书/钉钉/企微)
         ↓ Webhook
    Channel Adapter (解析平台消息格式)
         ↓ InboundMessage
    MessageBus (异步发布-订阅总线)
         ↓
    ChannelManager (路由 + 调度)
         ↓ 调用 AgentCore Agent API
    Agent 执行
         ↓ OutboundMessage
    MessageBus
         ↓
    Channel Adapter (序列化为平台消息格式)
         ↓ HTTP
    IM 平台

与 DeerFlow 的区别：
  - DeerFlow 调用 LangGraph SDK，AgentCore 调用自身的 FastAPI 接口
  - DeerFlow 将 channel 作为 harness 层，AgentCore 放在 app 层
  - AgentCore 渠道需要处理更复杂的业务（Pipeline 触发、资产管理等）
"""

from agent_core.channels.base import ChannelAdapter, InboundMessage, OutboundMessage
from agent_core.channels.message_bus import MessageBus
from agent_core.channels.manager import ChannelManager

__all__ = [
    "ChannelAdapter",
    "InboundMessage",
    "OutboundMessage",
    "MessageBus",
    "ChannelManager",
]
