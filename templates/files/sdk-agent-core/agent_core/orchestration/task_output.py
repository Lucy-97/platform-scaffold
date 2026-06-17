"""
Redis Stream 输出管道 — TaskOutputStream
==========================================

替代 Claude Code 的本地文件输出（outputFile + outputOffset）。
使用 Redis Stream 实现分布式增量读取，适配 Docker 集群环境。

设计要点：
  - XADD 追加：子进程/远端 MCP 可跨容器写入
  - XRANGE 增量读取：按 last_id 拉取新条目（类似 Claude 的 offset 游标）
  - 终态自动过期：24h 后清理，保留审计窗口
"""

from typing import Any, List

from loguru import logger


class TaskOutputStream:
    """统一的任务输出流——基于 Redis Stream。

    每个任务拥有独立的 Stream key（agent:task_output:{task_id}），
    子进程、远端 MCP 服务器、甚至不同容器都可以向同一个 Stream 追加输出。
    前端 SSE 可通过 XREAD BLOCK 实时订阅。

    Args:
        redis: redis.asyncio.Redis 连接实例。
        task_id: 任务 ID。
    """

    def __init__(self, redis: Any, task_id: str) -> None:
        self._redis = redis
        self._stream_key = f"agent:task_output:{task_id}"

    @property
    def stream_key(self) -> str:
        """获取 Redis Stream key。"""
        return self._stream_key

    async def append(self, content: str, level: str = "info") -> str:
        """追加一条输出记录。

        Args:
            content: 输出内容（如进度百分比、日志行、中间结果）。
            level: 日志级别（info / warn / error / progress）。

        Returns:
            Redis Stream 返回的条目 ID。
        """
        entry_id = await self._redis.xadd(
            self._stream_key,
            {"content": content, "level": level},
        )
        return entry_id

    async def read_since(
        self, last_id: str = "0-0", count: int = 50
    ) -> List[dict]:
        """增量读取输出（替代 Claude 的 outputOffset 游标机制）。

        Args:
            last_id: 上次读取的最后一条 ID（首次传 "0-0"）。
            count: 单次最大返回条目数。

        Returns:
            输出条目列表，每条包含 id / content / level。
        """
        # XRANGE 的 min 参数用 ( 前缀表示"开区间"（不包含 last_id 本身）
        entries = await self._redis.xrange(
            self._stream_key,
            min=f"({last_id}" if last_id != "0-0" else "-",
            count=count,
        )
        results = []
        for entry_id, fields in entries:
            # Redis 返回 bytes，需要 decode
            eid = entry_id.decode() if isinstance(entry_id, bytes) else entry_id
            decoded = {
                (k.decode() if isinstance(k, bytes) else k):
                (v.decode() if isinstance(v, bytes) else v)
                for k, v in fields.items()
            }
            results.append({"id": eid, **decoded})
        return results

    async def cleanup(self, ttl_seconds: int = 86400) -> None:
        """终态后设置过期时间——保留审计窗口而非立即删除。

        Args:
            ttl_seconds: 过期秒数，默认 24 小时。
        """
        await self._redis.expire(self._stream_key, ttl_seconds)
        logger.debug(
            f"[TaskOutputStream] 设置过期 {self._stream_key} "
            f"TTL={ttl_seconds}s"
        )
