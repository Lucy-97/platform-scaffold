"""
飞书渠道适配器 — FeishuAdapter
=================================

实现飞书 IM 的 ChannelAdapter 接口，处理：
  - Webhook 事件解析（消息接收、URL 验证）
  - 消息发送（通过飞书 OpenAPI）
  - 签名验证

飞书 Webhook 消息格式参考：
  https://open.feishu.cn/document/server-docs/im-v1/message/events/receive

注意：此适配器为框架性实现，实际使用前需配置：
  - FEISHU_APP_ID: 飞书应用 App ID
  - FEISHU_APP_SECRET: 飞书应用 App Secret
  - FEISHU_VERIFICATION_TOKEN: Webhook 验证 Token
"""

import hashlib
import hmac
import json
import os
import time
from typing import Any, Dict, Optional

from loguru import logger

from agent_core.channels.base import (
    ChannelAdapter, InboundMessage, OutboundMessage, MessageType,
)


class FeishuAdapter(ChannelAdapter):
    """飞书 IM 渠道适配器。

    处理飞书平台的 Webhook 事件和消息发送。

    Args:
        app_id: 飞书 App ID（默认从环境变量读取）。
        app_secret: 飞书 App Secret（默认从环境变量读取）。
        verification_token: Webhook 验证 Token。
    """

    def __init__(
        self,
        app_id: Optional[str] = None,
        app_secret: Optional[str] = None,
        verification_token: Optional[str] = None,
    ):
        self.app_id = app_id or os.getenv("FEISHU_APP_ID", "")
        self.app_secret = app_secret or os.getenv("FEISHU_APP_SECRET", "")
        self.verification_token = (
            verification_token or os.getenv("FEISHU_VERIFICATION_TOKEN", "")
        )
        # 缓存的 tenant_access_token 和过期时间
        self._access_token: str = ""
        self._token_expires_at: float = 0

    @property
    def channel_type(self) -> str:
        """飞书渠道标识。"""
        return "feishu"

    async def parse_webhook(
        self,
        payload: Dict[str, Any],
        headers: Optional[Dict[str, str]] = None,
    ) -> Optional[InboundMessage]:
        """解析飞书 Webhook 事件为标准 InboundMessage。

        处理两种主要事件类型：
          1. URL 验证（challenge）— 返回 None，由外层直接返回 challenge
          2. 消息接收 — 解析为 InboundMessage

        Args:
            payload: 飞书 Webhook JSON body。
            headers: HTTP 请求头。

        Returns:
            InboundMessage 或 None（验证/心跳事件）。
        """
        # 处理 URL 验证请求（飞书首次配置 Webhook 时发送）
        if "challenge" in payload:
            logger.info("[FeishuAdapter] URL verification challenge")
            return None

        # 提取事件信息
        header = payload.get("header", {})
        event_type = header.get("event_type", "")

        # 仅处理消息接收事件
        if event_type != "im.message.receive_v1":
            logger.debug(
                f"[FeishuAdapter] Ignoring event_type={event_type}"
            )
            return None

        event = payload.get("event", {})
        message = event.get("message", {})
        sender = event.get("sender", {}).get("sender_id", {})

        # 解析消息内容
        msg_type = message.get("message_type", "text")
        content_str = message.get("content", "{}")

        try:
            content_obj = json.loads(content_str)
        except json.JSONDecodeError:
            content_obj = {"text": content_str}

        # 提取纯文本内容
        text_content = ""
        if msg_type == "text":
            text_content = content_obj.get("text", "")
        else:
            # 非文本消息暂存原始 JSON
            text_content = content_str

        # 检查是否 @了机器人
        mentions = message.get("mentions", [])
        is_mentioned = len(mentions) > 0

        # 如果 @ 了机器人，去掉 @mention 占位符
        if is_mentioned and text_content:
            for mention in mentions:
                mention_key = mention.get("key", "")
                if mention_key:
                    text_content = text_content.replace(mention_key, "").strip()

        # 检查是否为命令
        message_type = MessageType.TEXT
        if text_content.startswith("/"):
            message_type = MessageType.COMMAND

        return InboundMessage(
            channel_type=self.channel_type,
            channel_id=message.get("chat_id", ""),
            sender_id=sender.get("open_id", ""),
            sender_name=sender.get("user_id", ""),
            message_id=message.get("message_id", ""),
            message_type=message_type,
            content=text_content,
            raw_payload=payload,
            thread_id=message.get("root_id"),  # 飞书话题 ID
            mentioned=is_mentioned,
        )

    async def send_message(self, message: OutboundMessage) -> bool:
        """通过飞书 OpenAPI 发送消息。

        Args:
            message: 标准出站消息。

        Returns:
            是否发送成功。
        """
        if not self.app_id or not self.app_secret:
            logger.warning(
                "[FeishuAdapter] FEISHU_APP_ID/FEISHU_APP_SECRET not configured"
            )
            return False

        try:
            import httpx

            # 获取 access_token
            token = await self._get_access_token()
            if not token:
                return False

            # 构建飞书消息体
            feishu_payload = {
                "receive_id": message.channel_id,
                "msg_type": "text",
                "content": json.dumps({"text": message.content}),
            }

            # 如果是回复消息
            if message.reply_to_message_id:
                feishu_payload["reply_in_thread"] = False

            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    "https://open.feishu.cn/open-apis/im/v1/messages",
                    params={"receive_id_type": "chat_id"},
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json; charset=utf-8",
                    },
                    json=feishu_payload,
                    timeout=10,
                )

            if resp.status_code == 200:
                data = resp.json()
                if data.get("code") == 0:
                    logger.info(
                        f"[FeishuAdapter] Message sent to {message.channel_id}"
                    )
                    return True
                else:
                    logger.error(
                        f"[FeishuAdapter] API error: {data.get('msg')}"
                    )
                    return False
            else:
                logger.error(
                    f"[FeishuAdapter] HTTP {resp.status_code}: {resp.text}"
                )
                return False

        except Exception as e:
            logger.error(f"[FeishuAdapter] Send failed: {e}")
            return False

    async def _get_access_token(self) -> str:
        """获取飞书 tenant_access_token（带缓存）。

        Token 有效期 2 小时，提前 5 分钟刷新。

        Returns:
            有效的 access_token 字符串，失败返回空字符串。
        """
        # 检查缓存是否有效
        if self._access_token and time.time() < self._token_expires_at:
            return self._access_token

        try:
            import httpx

            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
                    json={
                        "app_id": self.app_id,
                        "app_secret": self.app_secret,
                    },
                    timeout=10,
                )

            if resp.status_code == 200:
                data = resp.json()
                if data.get("code") == 0:
                    self._access_token = data.get("tenant_access_token", "")
                    # 提前 5 分钟过期
                    expire = data.get("expire", 7200)
                    self._token_expires_at = time.time() + expire - 300
                    return self._access_token

            logger.error(f"[FeishuAdapter] Failed to get access token")
            return ""

        except Exception as e:
            logger.error(f"[FeishuAdapter] Token request failed: {e}")
            return ""

    async def verify_signature(
        self,
        payload: bytes,
        headers: Dict[str, str],
    ) -> bool:
        """验证飞书 Webhook 签名。

        使用 HMAC-SHA256 验证请求合法性。

        Args:
            payload: 原始请求 body。
            headers: HTTP 请求头（需包含 X-Lark-Signature）。

        Returns:
            签名是否有效。
        """
        if not self.verification_token:
            # 未配置 token 时跳过验证（开发环境）
            return True

        signature = headers.get("X-Lark-Signature", "")
        timestamp = headers.get("X-Lark-Request-Timestamp", "")
        nonce = headers.get("X-Lark-Request-Nonce", "")

        # 拼接验证字符串
        sign_str = timestamp + nonce + self.verification_token
        # 加上 body
        sign_bytes = sign_str.encode("utf-8") + payload

        expected = hashlib.sha256(sign_bytes).hexdigest()

        return hmac.compare_digest(expected, signature)
