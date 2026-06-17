"""
渠道管理器 — ChannelManager
==============================

核心调度器，负责：
  1. 注册和管理多个 ChannelAdapter（飞书、钉钉、企微等）
  2. 路由入站消息到正确的处理链
  3. 管理 channel_id → thread_id 的映射（对话线程持久化）
  4. 调用 AgentCore Agent API 生成回复
  5. 将回复通过对应渠道发送回用户

借鉴 DeerFlow 2.0 ChannelManager 的路由-调度模式，
但使用 AgentCore 自身的 orchestrator API 而非 LangGraph SDK。
"""

import uuid
from collections import defaultdict
from typing import Any, Callable, Dict, List, Optional

from loguru import logger

from agent_core.channels.base import (
    ChannelAdapter, InboundMessage, OutboundMessage, MessageType,
)
from agent_core.channels.message_bus import MessageBus


class ChannelManager:
    """IM 渠道管理器。

    协调多个 ChannelAdapter 和 MessageBus，
    实现完整的 入站 → 处理 → 出站 消息流。

    Args:
        message_bus: 消息总线实例（可选，默认创建新的）。

    核心流程::

        1. ChannelAdapter.parse_webhook() → InboundMessage
        2. ChannelManager.handle_inbound() 
           → 查找/创建 thread_id
           → 调用 agent_handler 回调
        3. agent_handler 返回回复文本
        4. ChannelManager 构建 OutboundMessage
        5. ChannelAdapter.send_message()
    """

    def __init__(self, message_bus: Optional[MessageBus] = None):
        self.bus = message_bus or MessageBus()
        # channel_type → ChannelAdapter
        self._adapters: Dict[str, ChannelAdapter] = {}
        # (channel_type, channel_id) → thread_id 映射
        self._thread_map: Dict[str, str] = {}
        # Agent 处理回调
        self._agent_handler: Optional[Callable] = None

    def register_adapter(self, adapter: ChannelAdapter) -> None:
        """注册一个渠道适配器。

        Args:
            adapter: 渠道适配器实例。
        """
        self._adapters[adapter.channel_type] = adapter
        logger.info(
            f"[ChannelManager] Registered adapter: {adapter.channel_type}"
        )

    def set_agent_handler(self, handler: Callable) -> None:
        """设置 Agent 处理回调。

        回调签名::

            async def handler(
                thread_id: str,
                user_message: str,
                context: Dict[str, Any],
            ) -> str:
                '''返回 Agent 回复文本。'''

        Args:
            handler: 异步回调函数。
        """
        self._agent_handler = handler
        logger.info("[ChannelManager] Agent handler set")

    async def handle_webhook(
        self,
        channel_type: str,
        payload: Dict[str, Any],
        headers: Optional[Dict[str, str]] = None,
    ) -> Optional[Dict[str, Any]]:
        """处理来自 IM 平台的 Webhook 请求。

        完整流程：解析 → 路由 → Agent 处理 → 回复

        Args:
            channel_type: 渠道类型（如 "feishu"）。
            payload: Webhook 请求体。
            headers: HTTP 请求头。

        Returns:
            Webhook 响应（某些平台需要立即返回，如飞书 challenge 验证）。
        """
        adapter = self._adapters.get(channel_type)
        if not adapter:
            logger.warning(
                f"[ChannelManager] No adapter for channel_type={channel_type}"
            )
            return {"error": f"Unknown channel type: {channel_type}"}

        # 解析入站消息
        inbound = await adapter.parse_webhook(payload, headers)
        if inbound is None:
            # 非需处理的事件（如 challenge 验证、心跳等）
            return {"ok": True}

        logger.info(
            f"[ChannelManager] Inbound: channel={inbound.channel_type} "
            f"sender={inbound.sender_name}({inbound.sender_id}) "
            f"type={inbound.message_type.value}"
        )

        # 发布到消息总线（异步处理）
        await self.bus.publish("inbound", inbound)

        # 同步处理并回复
        await self._process_and_reply(inbound)

        return {"ok": True}

    async def _process_and_reply(self, inbound: InboundMessage) -> None:
        """处理入站消息并发送回复。

        Args:
            inbound: 归一化入站消息。
        """
        if not self._agent_handler:
            logger.warning("[ChannelManager] No agent handler set, skipping")
            return

        # 仅处理文本和命令消息
        if inbound.message_type not in (MessageType.TEXT, MessageType.COMMAND):
            return

        # 获取或创建对话线程 ID
        thread_id = self._get_or_create_thread(inbound)

        # 构建上下文
        context = {
            "channel_type": inbound.channel_type,
            "channel_id": inbound.channel_id,
            "sender_id": inbound.sender_id,
            "sender_name": inbound.sender_name,
            "thread_id": thread_id,
            "message_id": inbound.message_id,
        }

        try:
            # 调用 Agent 处理
            reply_text = await self._agent_handler(
                thread_id=thread_id,
                user_message=inbound.content,
                context=context,
            )
        except Exception as e:
            logger.error(
                f"[ChannelManager] Agent handler error: {e}",
                exc_info=True,
            )
            reply_text = "抱歉，处理您的消息时出现了错误，请稍后重试。"

        # 构建并发送出站消息
        outbound = OutboundMessage(
            channel_type=inbound.channel_type,
            channel_id=inbound.channel_id,
            content=reply_text,
            reply_to_message_id=inbound.message_id,
            thread_id=inbound.thread_id,
        )

        adapter = self._adapters.get(inbound.channel_type)
        if adapter:
            try:
                success = await adapter.send_message(outbound)
                if not success:
                    logger.warning(
                        f"[ChannelManager] Failed to send reply to "
                        f"{inbound.channel_type}/{inbound.channel_id}"
                    )
            except Exception as e:
                logger.error(
                    f"[ChannelManager] Send error: {e}",
                    exc_info=True,
                )

        # 发布出站消息到总线（用于日志/审计）
        await self.bus.publish("outbound", outbound)

    def _get_or_create_thread(self, inbound: InboundMessage) -> str:
        """获取或创建对话线程 ID。

        使用 (channel_type, channel_id) 作为 key 映射到 thread_id。
        同一个群/频道共享一个 thread_id（可按需改为 per-sender）。

        Args:
            inbound: 入站消息。

        Returns:
            对话线程 ID。
        """
        # 优先使用消息自带的 thread_id（如飞书话题）
        if inbound.thread_id:
            return inbound.thread_id

        key = f"{inbound.channel_type}:{inbound.channel_id}"
        if key not in self._thread_map:
            self._thread_map[key] = uuid.uuid4().hex[:16]
            logger.info(
                f"[ChannelManager] New thread: {key} → "
                f"{self._thread_map[key]}"
            )

        return self._thread_map[key]

    def reset_thread(self, channel_type: str, channel_id: str) -> None:
        """重置指定渠道的对话线程（开始新对话）。

        通常由 /reset 命令触发。

        Args:
            channel_type: 渠道类型。
            channel_id: 渠道 ID。
        """
        key = f"{channel_type}:{channel_id}"
        old = self._thread_map.pop(key, None)
        if old:
            logger.info(f"[ChannelManager] Thread reset: {key} (was {old})")

    @property
    def registered_channels(self) -> List[str]:
        """返回已注册的渠道类型列表。"""
        return list(self._adapters.keys())
