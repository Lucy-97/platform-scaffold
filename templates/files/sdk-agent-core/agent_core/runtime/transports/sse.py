"""
SSETransport — Server-Sent Events 传输适配器
=============================================

将 RuntimeEvent 序列化为 SSE (text/event-stream) 格式，
适用于 HTTP 流式响应（FastAPI StreamingResponse / aiohttp）。

SSE 格式::

    event: stream_delta
    data: {"type":"stream_delta","data":"你好","turn":1}

    event: agent_handoff
    data: {"type":"agent_handoff","data":{"from":"supervisor","to":"researcher"}}

集成示例 (FastAPI)::

    from fastapi.responses import StreamingResponse
    from agent_core.runtime.transports import SSETransport

    @app.post("/chat/stream")
    async def chat_stream(req: ChatRequest):
        transport = SSETransport()
        events = engine.submit(req.message)
        return StreamingResponse(
            transport.stream(events),
            media_type="text/event-stream",
        )
"""

import json
from typing import AsyncGenerator, Set

from agent_core.runtime.events import RuntimeEvent, RuntimeEventType
from agent_core.runtime.transports.base import TransportAdapter


# 终态事件——发送后关闭连接
_TERMINAL_EVENTS: Set[RuntimeEventType] = {
    RuntimeEventType.RESULT,
    RuntimeEventType.ERROR,
    RuntimeEventType.INTERRUPTED,
}


class SSETransport(TransportAdapter):
    """SSE (Server-Sent Events) 传输适配器。

    特性：
      - 每个 RuntimeEvent 序列化为一条 SSE 消息
      - event 字段使用事件类型名（方便前端 addEventListener 按类型监听）
      - data 字段为 JSON 字符串
      - 终态事件后自动发送 [DONE] 信号

    Args:
        include_metadata: 是否在 SSE data 中包含 metadata 字段。
    """

    def __init__(self, include_metadata: bool = True) -> None:
        self._include_metadata = include_metadata

    async def stream(
        self,
        events: AsyncGenerator[RuntimeEvent, None],
    ) -> AsyncGenerator[str, None]:
        """将 RuntimeEvent 流转换为 SSE 格式字符串流。

        Args:
            events: 引擎产生的事件流。

        Yields:
            SSE 格式字符串（含换行符）。
        """
        async for event in events:
            yield self._format_event(event)

            # 终态事件后发送 SSE 标准结束信号
            if event.type in _TERMINAL_EVENTS:
                yield "data: [DONE]\n\n"
                return

    def _format_event(self, event: RuntimeEvent) -> str:
        """将单个 RuntimeEvent 格式化为 SSE 消息。"""
        payload = {
            "type": event.type.value,
            "data": event.data,
            "turn": event.turn,
            "timestamp": event.timestamp,
        }
        if self._include_metadata and event.metadata:
            payload["metadata"] = event.metadata

        # SSE 格式: event + data + 空行分隔
        lines = [
            f"event: {event.type.value}",
            f"data: {json.dumps(payload, ensure_ascii=False)}",
            "",  # 空行分隔消息
            "",
        ]
        return "\n".join(lines)
