"""
Redis Hash 持久化层 — TaskStore
================================

将任务状态持久化到 Redis Hash，支持多 Worker 共享状态。

设计决策：
  - 使用 Redis Hash 而非内存单例：多 Gunicorn Worker 共享、重启不丢失
  - 按 project_id 建立 Set 索引：前端 API 可按项目查询所有任务
  - 原子 HSET 更新：单字段更新不会覆盖其他字段
"""

import time
from typing import List, Optional

from loguru import logger

from .task_state import AgentTaskState, AgentTaskStatus, is_terminal


# Redis key 模式
_TASK_HASH_KEY = "agent:task:{task_id}"
_PROJECT_INDEX_KEY = "agent:project_tasks:{project_id}"
# 终态任务 Hash 过期时间（避免 Redis 累积增长）
_TERMINAL_TTL_SECONDS = 86400 * 7   # 7 天


class TaskStore:
    """基于 Redis Hash 的任务状态存储。

    Args:
        redis: redis.asyncio.Redis 连接实例（来自全局连接池）。
    """

    def __init__(self, redis) -> None:
        self._redis = redis

    async def register(self, task: AgentTaskState) -> None:
        """注册新任务——写入 Hash + 按 project_id 索引。"""
        key = _TASK_HASH_KEY.format(task_id=task.id)
        # Pydantic V2 序列化为 JSON-safe dict
        data = task.model_dump(mode="json")
        # Redis Hash 要求 value 为 str，把嵌套的 None 转为空串
        flat = {k: str(v) if v is not None else "" for k, v in data.items()}
        await self._redis.hset(key, mapping=flat)

        # 按项目索引
        if task.project_id:
            idx_key = _PROJECT_INDEX_KEY.format(project_id=task.project_id)
            await self._redis.sadd(idx_key, task.id)

        logger.info(
            f"[TaskStore] 注册任务 {task.id} ({task.task_type.value}): "
            f"{task.description[:60]}"
        )

    async def update_status(
        self,
        task_id: str,
        status: AgentTaskStatus,
        result_summary: str = "",
        error_message: str = "",
    ) -> None:
        """原子更新任务状态。终态时自动设置 end_time 和 TTL。"""
        key = _TASK_HASH_KEY.format(task_id=task_id)
        updates: dict = {"status": status.value}

        if is_terminal(status):
            updates["end_time"] = str(time.time())
        if result_summary:
            updates["result_summary"] = result_summary
        if error_message:
            updates["error_message"] = error_message

        await self._redis.hset(key, mapping=updates)

        # 终态任务设置过期时间，防止 Redis 无限增长
        if is_terminal(status):
            await self._redis.expire(key, _TERMINAL_TTL_SECONDS)

        logger.debug(f"[TaskStore] 更新 {task_id} → {status.value}")

    async def get(self, task_id: str) -> Optional[AgentTaskState]:
        """获取单个任务状态。"""
        key = _TASK_HASH_KEY.format(task_id=task_id)
        data = await self._redis.hgetall(key)
        if not data:
            return None
        # Redis Hash 返回的是 bytes，转为 str
        decoded = {
            (k.decode() if isinstance(k, bytes) else k):
            (v.decode() if isinstance(v, bytes) else v)
            for k, v in data.items()
        }
        return _parse_task_state(decoded)

    async def get_completed_unnotified(
        self, project_id: str
    ) -> List[AgentTaskState]:
        """获取已完成但未通知主 Agent 的任务——主循环每轮前调用。

        这是整套系统的"回注触发器"：主循环检查这个列表，
        有结果就构造通知消息注入 LLM 消息队列。
        """
        idx_key = _PROJECT_INDEX_KEY.format(project_id=project_id)
        task_ids = await self._redis.smembers(idx_key)
        results: List[AgentTaskState] = []

        for tid_raw in task_ids:
            tid = tid_raw.decode() if isinstance(tid_raw, bytes) else tid_raw
            task = await self.get(tid)
            if task and is_terminal(task.status) and not task.notified:
                results.append(task)

        return results

    async def mark_notified(self, task_id: str) -> None:
        """标记任务已通知——防重复回注。"""
        key = _TASK_HASH_KEY.format(task_id=task_id)
        await self._redis.hset(key, "notified", "True")

    async def list_by_project(self, project_id: str) -> List[AgentTaskState]:
        """按项目列出所有任务——前端任务管理器 API 用。"""
        idx_key = _PROJECT_INDEX_KEY.format(project_id=project_id)
        task_ids = await self._redis.smembers(idx_key)
        results: List[AgentTaskState] = []

        for tid_raw in task_ids:
            tid = tid_raw.decode() if isinstance(tid_raw, bytes) else tid_raw
            task = await self.get(tid)
            if task:
                results.append(task)

        return results


def _parse_task_state(data: dict) -> AgentTaskState:
    """从 Redis Hash 扁平 dict 解析回 Pydantic 模型。

    Redis 中 bool 存为 "True"/"False" 字符串，
    None 存为空串，需要特殊处理。
    """
    # bool 字段修复
    if "notified" in data:
        data["notified"] = data["notified"] in ("True", "true", "1")

    # 可选 float 字段修复
    for field_name in ("start_time", "end_time"):
        val = data.get(field_name, "")
        if val and val != "None":
            data[field_name] = float(val)
        elif field_name == "end_time":
            data[field_name] = None

    # 可选 str 字段：空串视为 None
    for field_name in (
        "project_id", "user_id", "triggered_by_tool_id",
        "result_summary", "error_message",
    ):
        if data.get(field_name) in ("", "None"):
            data[field_name] = None

    return AgentTaskState(**data)
