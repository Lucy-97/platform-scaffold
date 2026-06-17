"""
五层记忆数据模型 — MemoryEntry / MemoryLayer
===============================================

通用的分层记忆架构，支持任意领域（创作、编程、数据分析等）。
具体的业务语义（如"角色设定""世界观"）不在此层定义，
而是由上层应用通过 metadata 或自定义 MemoryCategory 扩展。

五层分级（通用）：
  L1 Session    — 当前会话状态（断连恢复用）
  L2 Task       — 单任务/单轮次记忆（完成即归档）
  L3 Project    — 项目级持久化（跨会话共享）
  L4 User       — 用户级偏好与行为（跨项目持久）
  L5 Global     — 全局共享知识（规则/模板/最佳实践）
"""

import time
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class MemoryLayer(str, Enum):
    """记忆层级枚举——五层通用分级。"""
    SESSION = "L1_session"     # 会话级——最短生命周期
    TASK = "L2_task"           # 任务级——完成即归档
    PROJECT = "L3_project"     # 项目级——跨会话持久
    USER = "L4_user"           # 用户级——跨项目持久
    GLOBAL = "L5_global"       # 全局级——最长生命周期


class MemoryCategory(str, Enum):
    """记忆类型分类——通用分类，领域无关。

    上层应用可通过 metadata 字段扩展领域特定语义，
    而不需要修改此枚举。
    """
    ENTITY = "entity"           # 实体信息（人/物/概念的属性描述）
    RELATION = "relation"       # 实体间关系
    EVENT = "event"             # 事件记录（发生了什么）
    DECISION = "decision"       # 决策记录（做了什么选择、为什么）
    PREFERENCE = "preference"   # 偏好/配置
    PATTERN = "pattern"         # 行为模式/规律
    STATE = "state"             # 状态快照（会话黑板等）
    ARTIFACT = "artifact"       # 产出物引用（文件/链接/结果）
    NOTE = "note"               # 自由文本备注


class MemoryEntry(BaseModel):
    """单条记忆条目——五层记忆体系的基本单元。

    领域无关的通用结构。业务特定语义通过 metadata 字段承载，
    例如：metadata={"domain": "drama", "episode": "ep03"}

    Attributes:
        memory_id: 唯一标识符。
        layer: 所属层级（L1~L5）。
        category: 记忆类型。
        content: 记忆内容（自然语言描述）。
        subject: 主体（相关实体名）。
        confidence: 置信度 (0.0~1.0)，用于冲突消解。
        source_turn: 来源轮次号。
        source_session: 来源会话 ID。
        project_id: 所属项目 ID。
        task_id: 所属任务 ID。
        created_at: 创建时间戳。
        updated_at: 最近更新时间戳。
        access_count: 被检索到的次数（用于冷门淘汰）。
        ttl_seconds: 生存时间（0 = 永不过期）。
        superseded_by: 被覆盖的新记忆 ID（冲突消解链）。
        metadata: 额外元数据（领域特定语义在此扩展）。
    """
    memory_id: str = Field(description="唯一标识符")
    layer: MemoryLayer = Field(description="所属层级")
    category: MemoryCategory = Field(description="记忆类型")
    content: str = Field(description="记忆内容")
    subject: str = Field(default="", description="记忆主体")
    confidence: float = Field(default=0.8, ge=0.0, le=1.0, description="置信度")

    source_turn: int = Field(default=0, description="来源轮次")
    source_session: str = Field(default="", description="来源会话 ID")
    project_id: str = Field(default="", description="所属项目")
    task_id: str = Field(default="", description="所属任务")

    created_at: float = Field(default_factory=time.time, description="创建时间戳")
    updated_at: float = Field(default_factory=time.time, description="更新时间戳")
    access_count: int = Field(default=0, description="访问次数")
    ttl_seconds: int = Field(default=0, description="生存时间(0=永不过期)")

    superseded_by: Optional[str] = Field(
        default=None, description="被覆盖者的 ID"
    )
    metadata: Dict[str, Any] = Field(
        default_factory=dict, description="领域扩展元数据"
    )

    @property
    def is_expired(self) -> bool:
        """检查记忆是否已过期。"""
        if self.ttl_seconds <= 0:
            return False
        return (time.time() - self.created_at) > self.ttl_seconds

    @property
    def age_hours(self) -> float:
        """记忆年龄（小时）。"""
        return (time.time() - self.created_at) / 3600

    def to_injection_text(self) -> str:
        """生成可注入到 LLM System Prompt 的文本格式。"""
        prefix = f"[{self.category.value}]"
        if self.subject:
            prefix += f" {self.subject}:"
        return f"{prefix} {self.content}"
