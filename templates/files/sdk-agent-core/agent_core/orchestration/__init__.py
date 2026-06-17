"""
后台任务编排模块 — orchestration
=================================

借鉴 Claude Code CLI 的 Task 统一抽象，
在多租户 Web 架构下实现基于 Redis 的异步任务编排系统。

核心组件：
  - AgentTaskState / AgentTaskType / AgentTaskStatus: 统一 5 态状态模型
  - TaskStore: Redis Hash 持久化层
  - TaskOutputStream: Redis Stream 输出管道
  - CascadingCancel: 级联取消链
  - inject_completed_notifications: 结果回注主 Agent
"""

from .cancellation import CascadingCancel
from .notification import build_task_notification, inject_completed_notifications
from .task_output import TaskOutputStream
from .task_state import (
    AgentTaskStatus,
    AgentTaskType,
    AgentTaskState,
    generate_task_id,
    is_terminal,
)
from .task_store import TaskStore

__all__ = [
    # 状态模型
    "AgentTaskState",
    "AgentTaskType",
    "AgentTaskStatus",
    "is_terminal",
    "generate_task_id",
    # 持久化
    "TaskStore",
    # 输出
    "TaskOutputStream",
    # 取消
    "CascadingCancel",
    # 通知
    "build_task_notification",
    "inject_completed_notifications",
]
