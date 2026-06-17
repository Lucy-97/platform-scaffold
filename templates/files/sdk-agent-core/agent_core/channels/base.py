"""
渠道抽象基类 — Channel Adapter 和消息模型
============================================

定义 IM 渠道适配器的统一接口和标准消息模型。
所有具体渠道（飞书、钉钉、企微等）均继承 ChannelAdapter 实现。

消息流转模型::

    InboundMessage  — IM 平台发来的用户消息（已归一化）
    OutboundMessage — 要发送到 IM 平台的回复消息（已归一化）

渠道适配器职责：
  1. parse_webhook(): 解析平台 Webhook 请求为 InboundMessage
  2. send_message(): 将 OutboundMessage 序列化为平台 API 格式并发送
  3. verify_signature(): 验证 Webhook 签名（安全）
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional


class MessageType(str, Enum):
    """消息类型枚举。"""
    TEXT = "text"           # 纯文本
    IMAGE = "image"         # 图片（URL）
    FILE = "file"           # 文件（URL）
    CARD = "card"           # 卡片/富文本
    COMMAND = "command"     # 命令（如 /help、/reset）
    EVENT = "event"         # 平台事件（如群成员变化）


@dataclass
class InboundMessage:
    """从 IM 平台收到的归一化入站消息。

    不同平台的消息格式各异，适配器负责将其转换为此统一格式。

    Attributes:
        channel_type: 渠道类型标识（如 "feishu"、"dingtalk"）。
        channel_id: 渠道内的群/频道/会话 ID。
        sender_id: 发送者在该平台的唯一 ID。
        sender_name: 发送者显示名称。
        message_id: 平台消息唯一 ID（用于去重和回复定位）。
        message_type: 消息类型。
        content: 消息内容（文本/URL/命令参数等）。
        raw_payload: 原始 Webhook 请求体（调试用）。
        timestamp: 消息时间戳。
        thread_id: 话题/线程 ID（用于关联多轮对话）。
        mentioned: 是否 @了机器人（某些平台要求 @ 才响应）。
        extra: 平台特有的额外字段。
    """
    channel_type: str
    channel_id: str
    sender_id: str
    sender_name: str = ""
    message_id: str = ""
    message_type: MessageType = MessageType.TEXT
    content: str = ""
    raw_payload: Dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    thread_id: Optional[str] = None
    mentioned: bool = False
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class OutboundMessage:
    """要发送到 IM 平台的归一化出站消息。

    Attributes:
        channel_type: 目标渠道类型。
        channel_id: 目标群/频道/会话 ID。
        message_type: 消息类型。
        content: 消息内容。
        reply_to_message_id: 回复的原始消息 ID（可选）。
        thread_id: 话题/线程 ID。
        extra: 平台特有的额外字段（如飞书卡片 JSON）。
    """
    channel_type: str
    channel_id: str
    message_type: MessageType = MessageType.TEXT
    content: str = ""
    reply_to_message_id: Optional[str] = None
    thread_id: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)


class ChannelAdapter(ABC):
    """IM 渠道适配器抽象基类。

    每个具体渠道（飞书、钉钉、企微等）实现此接口。
    适配器负责「协议翻译」——将平台特定格式与 AgentCore 标准消息模型互转。

    子类实现示例::

        class FeishuAdapter(ChannelAdapter):
            @property
            def channel_type(self) -> str:
                return "feishu"

            async def parse_webhook(self, payload):
                return InboundMessage(...)

            async def send_message(self, msg):
                await self._call_feishu_api(msg)
    """

    @property
    @abstractmethod
    def channel_type(self) -> str:
        """渠道类型标识符（如 "feishu"、"dingtalk"）。"""
        ...

    @abstractmethod
    async def parse_webhook(
        self,
        payload: Dict[str, Any],
        headers: Optional[Dict[str, str]] = None,
    ) -> Optional[InboundMessage]:
        """解析平台 Webhook 请求为标准入站消息。

        Args:
            payload: Webhook 请求 body（JSON parsed）。
            headers: HTTP 请求头（用于签名验证）。

        Returns:
            解析后的 InboundMessage，若为不需处理的事件则返回 None。
        """
        ...

    @abstractmethod
    async def send_message(self, message: OutboundMessage) -> bool:
        """发送出站消息到 IM 平台。

        Args:
            message: 要发送的标准出站消息。

        Returns:
            是否发送成功。
        """
        ...

    async def verify_signature(
        self,
        payload: bytes,
        headers: Dict[str, str],
    ) -> bool:
        """验证 Webhook 请求签名（默认不验证）。

        具体渠道可覆盖此方法实现平台特定的签名验证逻辑。

        Args:
            payload: 原始请求 body bytes。
            headers: HTTP 请求头。

        Returns:
            签名是否有效。
        """
        return True
