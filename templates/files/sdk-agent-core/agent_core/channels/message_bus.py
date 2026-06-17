"""
异步消息总线 — MessageBus
===========================

借鉴 DeerFlow 2.0 的 MessageBus 发布-订阅模式。
解耦渠道适配器与业务逻辑，支持多渠道并行处理。

设计要点：
  - 基于 asyncio.Queue 的异步发布-订阅
  - 按 topic 隔离不同类型的消息流
  - 消费者注册为异步回调函数
  - 后台 worker 自动分发消息
  - 消费者异常不影响其他消费者
"""

import asyncio
from collections import defaultdict
from typing import Any, Callable, Dict, List, Optional

from loguru import logger


# 消费者回调类型：async def(message: Any) -> None
ConsumerCallback = Callable[[Any], Any]


class MessageBus:
    """异步发布-订阅消息总线。

    支持多 topic 消息路由，每个 topic 可注册多个消费者。
    消息发布到 topic 后，所有订阅该 topic 的消费者都会收到。

    Args:
        max_queue_size: 每个 topic 的消息队列最大长度（防止内存溢出）。

    Usage::

        bus = MessageBus()
        bus.subscribe("inbound", handler_fn)
        await bus.start()

        await bus.publish("inbound", some_message)

        # 关闭时
        await bus.stop()
    """

    def __init__(self, max_queue_size: int = 1000):
        self._max_queue_size = max_queue_size
        # topic → List[ConsumerCallback]
        self._consumers: Dict[str, List[ConsumerCallback]] = defaultdict(list)
        # topic → asyncio.Queue
        self._queues: Dict[str, asyncio.Queue] = {}
        # topic → asyncio.Task (后台 worker)
        self._workers: Dict[str, asyncio.Task] = {}
        self._running = False

    def subscribe(self, topic: str, callback: ConsumerCallback) -> None:
        """注册消费者回调到指定 topic。

        Args:
            topic: 消息主题（如 "inbound"、"outbound"）。
            callback: 异步消费者函数。
        """
        self._consumers[topic].append(callback)
        logger.info(
            f"[MessageBus] Subscribed to '{topic}': "
            f"{getattr(callback, '__name__', str(callback))}"
        )

    async def publish(self, topic: str, message: Any) -> None:
        """发布消息到指定 topic。

        消息会被放入 topic 对应的异步队列，由后台 worker 分发给消费者。
        若总线未启动，消息仍会入队，待 start() 后处理。

        Args:
            topic: 消息主题。
            message: 消息对象（任意类型）。
        """
        queue = self._get_or_create_queue(topic)
        try:
            queue.put_nowait(message)
        except asyncio.QueueFull:
            logger.warning(
                f"[MessageBus] Queue full for topic '{topic}', "
                f"dropping message"
            )

    async def start(self) -> None:
        """启动所有 topic 的后台 worker。

        每个 topic 一个 worker，持续从队列取消息并分发。
        """
        if self._running:
            return

        self._running = True

        # 为已注册的 topic 创建 worker
        for topic in self._consumers:
            self._ensure_worker(topic)

        logger.info(
            f"[MessageBus] Started with {len(self._workers)} workers"
        )

    async def stop(self) -> None:
        """停止所有后台 worker。"""
        self._running = False

        for topic, task in self._workers.items():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        self._workers.clear()
        logger.info("[MessageBus] Stopped")

    def _get_or_create_queue(self, topic: str) -> asyncio.Queue:
        """获取或创建 topic 对应的消息队列。

        Args:
            topic: 消息主题。

        Returns:
            asyncio.Queue 实例。
        """
        if topic not in self._queues:
            self._queues[topic] = asyncio.Queue(maxsize=self._max_queue_size)
        return self._queues[topic]

    def _ensure_worker(self, topic: str) -> None:
        """确保 topic 有对应的后台 worker 任务。

        Args:
            topic: 消息主题。
        """
        if topic in self._workers and not self._workers[topic].done():
            return

        self._get_or_create_queue(topic)
        self._workers[topic] = asyncio.create_task(
            self._worker_loop(topic),
            name=f"messagebus-{topic}",
        )

    async def _worker_loop(self, topic: str) -> None:
        """后台 worker：持续从队列取消息并分发给所有消费者。

        单个消费者异常不影响其他消费者的消息接收。

        Args:
            topic: 消息主题。
        """
        queue = self._queues[topic]
        consumers = self._consumers.get(topic, [])

        logger.info(
            f"[MessageBus] Worker started for topic '{topic}' "
            f"({len(consumers)} consumers)"
        )

        while self._running:
            try:
                # 使用超时避免永久阻塞（便于优雅关闭）
                message = await asyncio.wait_for(queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

            # 分发给所有消费者
            for consumer in consumers:
                try:
                    await consumer(message)
                except Exception as e:
                    consumer_name = getattr(consumer, "__name__", str(consumer))
                    logger.error(
                        f"[MessageBus] Consumer '{consumer_name}' error "
                        f"on topic '{topic}': {e}",
                        exc_info=True,
                    )

    @property
    def topic_stats(self) -> Dict[str, Dict[str, int]]:
        """返回各 topic 的统计信息（消费者数、队列大小）。"""
        stats = {}
        for topic in set(list(self._consumers.keys()) + list(self._queues.keys())):
            queue = self._queues.get(topic)
            stats[topic] = {
                "consumers": len(self._consumers.get(topic, [])),
                "queue_size": queue.qsize() if queue else 0,
            }
        return stats
