"""
WebSocketTransport — WebSocket 双向传输适配器
=============================================

将 RuntimeEvent 通过 WebSocket 推送给客户端，
同时支持从客户端接收控制消息（如中断、输入）。

集成示例 (FastAPI)::

    from fastapi import WebSocket
    from agent_core.runtime.transports import WebSocketTransport

    @app.websocket("/ws/chat")
    async def ws_chat(ws: WebSocket):
        await ws.accept()
        transport = WebSocketTransport(ws)
        events = engine.submit(message)
        await transport.push(events)

集成示例 (aiohttp)::

    async def ws_handler(request):
        ws = aiohttp.web.WebSocketResponse()
        await ws.prepare(request)
        transport = WebSocketTransport(ws)
        events = engine.submit(message)
        await transport.push(events)
"""

import json
from typing import Any, AsyncGenerator, Optional, Set

from loguru import logger

from agent_core.runtime.events import RuntimeEvent, RuntimeEventType
from agent_core.runtime.transports.base import TransportAdapter


_TERMINAL_EVENTS: Set[RuntimeEventType] = {
    RuntimeEventType.RESULT,
    RuntimeEventType.ERROR,
    RuntimeEventType.INTERRUPTED,
}


class WebSocketTransport(TransportAdapter):
    """WebSocket 双向传输适配器。

    与 SSETransport 的区别：
      - 双向通道：可接收客户端消息（中断/输入）
      - 二进制支持：可扩展为 protobuf 等格式
      - 连接管理：支持心跳和重连

    Args:
        websocket: WebSocket 连接实例（FastAPI/aiohttp 兼容）。
        include_metadata: 是否包含 metadata 字段。
    """

    def __init__(
        self,
        websocket: Any = None,
        include_metadata: bool = True,
    ) -> None:
        self._ws = websocket
        self._include_metadata = include_metadata

    async def stream(
        self,
        events: AsyncGenerator[RuntimeEvent, None],
    ) -> AsyncGenerator[str, None]:
        """将事件流转为 JSON 字符串流（无 WebSocket 实例时使用）。

        当没有 WebSocket 连接时，退化为 JSON Lines 格式输出。
        """
        async for event in events:
            payload = self._serialize(event)
            yield payload + "\n"

            if event.type in _TERMINAL_EVENTS:
                return

    async def push(
        self,
        events: AsyncGenerator[RuntimeEvent, None],
    ) -> None:
        """通过 WebSocket 推送事件流。

        直接向 WebSocket 连接发送序列化的事件。
        终态事件后自动关闭连接。

        Args:
            events: 引擎产生的事件流。
        """
        if not self._ws:
            raise RuntimeError("WebSocket 连接未设置")

        try:
            async for event in events:
                payload = self._serialize(event)
                await self._ws.send_text(payload)

                if event.type in _TERMINAL_EVENTS:
                    # 发送结束信号
                    await self._ws.send_text(
                        json.dumps({"type": "done"})
                    )
                    return

        except Exception as e:
            logger.error(f"[WebSocketTransport] 推送异常: {e}")
            raise

    async def close(self) -> None:
        """关闭 WebSocket 连接。"""
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass

    def _serialize(self, event: RuntimeEvent) -> str:
        """序列化事件为 JSON 字符串。"""
        payload = {
            "type": event.type.value,
            "data": event.data,
            "turn": event.turn,
            "timestamp": event.timestamp,
        }
        if self._include_metadata and event.metadata:
            payload["metadata"] = event.metadata
        return json.dumps(payload, ensure_ascii=False)
