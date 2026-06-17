"""
TransportAdapter — 事件流传输抽象基类
======================================

所有 Transport 实现必须继承此抽象类。
Transport 的职责是消费 AsyncGenerator[RuntimeEvent] 并推送到目标通道。
"""

from abc import ABC, abstractmethod
from typing import Any, AsyncGenerator

from agent_core.runtime.events import RuntimeEvent


class TransportAdapter(ABC):
    """事件流传输适配器抽象基类。

    子类实现 stream() 方法，将 RuntimeEvent 序列化为目标协议格式。

    Usage::

        transport = SSETransport()
        async for chunk in transport.stream(engine.submit("hi")):
            response.write(chunk)
    """

    @abstractmethod
    async def stream(
        self,
        events: AsyncGenerator[RuntimeEvent, None],
    ) -> AsyncGenerator[str, None]:
        """将事件流转换为目标协议的字符串流。

        Args:
            events: RuntimeEvent 异步生成器。

        Yields:
            序列化后的字符串（SSE 格式 / JSON 帧等）。
        """
        ...

    async def close(self) -> None:
        """关闭传输通道（可选清理）。"""
        pass
