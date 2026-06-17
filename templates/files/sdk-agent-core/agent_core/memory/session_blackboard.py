"""
会话黑板 — SessionBlackboard
===============================

L1 层记忆的核心实现——结构化的会话状态快照。

解决问题：
  用户断连/刷新后，Agent 丢失全部上下文。
  有了 SessionBlackboard，Agent 可以从结构化状态恢复。

黑板内容：
  - 当前创作目标和阶段
  - 已完成的工作清单
  - 活跃的角色/场景/道具
  - 未解决的悬念和伏笔
  - 最近的工具调用结果
"""

import json
import time
from typing import Any, Dict, List, Optional

from loguru import logger
from pydantic import BaseModel, Field


class BlackboardSlot(BaseModel):
    """黑板上的一个数据槽位。"""
    key: str = Field(description="槽位键名")
    value: Any = Field(description="槽位值")
    updated_at: float = Field(default_factory=time.time)
    source: str = Field(default="system", description="来源(system/llm/user/tool)")


class SessionBlackboard:
    """会话黑板——L1 层结构化状态管理。

    提供类似字典的接口，但带有历史追踪和序列化能力。
    每次 set() 都记录来源和时间，支持完整的状态恢复。

    Args:
        session_id: 会话 ID。
        max_history: 每个 key 保留的历史版本数（默认 5）。
    """

    def __init__(
        self,
        session_id: str,
        max_history: int = 5,
    ) -> None:
        self.session_id = session_id
        self._max_history = max_history
        # 当前状态
        self._slots: Dict[str, BlackboardSlot] = {}
        # 历史版本：key → [旧值列表]
        self._history: Dict[str, List[BlackboardSlot]] = {}
        self._created_at = time.time()

    def set(
        self,
        key: str,
        value: Any,
        source: str = "system",
    ) -> None:
        """设置/更新黑板槽位。"""
        # 保存旧版本到历史
        if key in self._slots:
            if key not in self._history:
                self._history[key] = []
            self._history[key].append(self._slots[key])
            # 限制历史数量
            if len(self._history[key]) > self._max_history:
                self._history[key] = self._history[key][-self._max_history:]

        self._slots[key] = BlackboardSlot(
            key=key, value=value, source=source,
        )

    def get(self, key: str, default: Any = None) -> Any:
        """获取槽位值。"""
        slot = self._slots.get(key)
        return slot.value if slot else default

    def delete(self, key: str) -> bool:
        """删除槽位。"""
        if key in self._slots:
            del self._slots[key]
            return True
        return False

    def keys(self) -> List[str]:
        """所有槽位键名。"""
        return list(self._slots.keys())

    def to_snapshot(self) -> Dict[str, Any]:
        """导出完整的状态快照（可持久化）。"""
        return {
            "session_id": self.session_id,
            "created_at": self._created_at,
            "snapshot_at": time.time(),
            "slots": {
                k: {"value": s.value, "source": s.source, "updated_at": s.updated_at}
                for k, s in self._slots.items()
            },
        }

    def restore_from_snapshot(self, snapshot: Dict[str, Any]) -> None:
        """从快照恢复状态。"""
        self._slots.clear()
        for key, data in snapshot.get("slots", {}).items():
            self._slots[key] = BlackboardSlot(
                key=key,
                value=data["value"],
                source=data.get("source", "restored"),
                updated_at=data.get("updated_at", time.time()),
            )
        logger.info(
            f"[Blackboard] 从快照恢复 {len(self._slots)} 个槽位 "
            f"(session={self.session_id})"
        )

    def to_injection_text(self) -> str:
        """生成可注入到 System Prompt 的状态摘要。"""
        if not self._slots:
            return ""

        lines = ["[会话状态黑板]"]
        for key, slot in self._slots.items():
            val = slot.value
            # 截断过长的值
            val_str = str(val)
            if len(val_str) > 200:
                val_str = val_str[:200] + "..."
            lines.append(f"  - {key}: {val_str}")

        return "\n".join(lines)

    @property
    def size(self) -> int:
        """当前槽位数。"""
        return len(self._slots)
