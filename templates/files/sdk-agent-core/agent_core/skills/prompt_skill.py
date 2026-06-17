"""
Prompt 技能数据模型 — PromptSkill
=====================================

与现有 skill_loader.py（面向 Docker 沙箱代码执行）互补，
定义纯 Markdown 知识注入的"Prompt 技能"模型。

两条技能赛道：
  - 沙箱代码技能（skill_loader.py）：生成代码 → Docker 容器执行
  - Prompt 知识技能（本模块）：Markdown 知识 → 注入 System Prompt

Prompt 技能的使用者：
  - 产品经理可以通过写 SKILL.md 扩展 Agent 创作能力
  - 无需编写代码，无需 Docker
"""

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class SkillTrustLevel(str, Enum):
    """Prompt 技能信任等级——三级安全控制。

    决定技能注入到 System Prompt 时的限制程度：
      - BUILTIN: 内置技能，无限制，完全信任
      - PROJECT: 项目级技能，允许使用项目上下文
      - USER: 用户自定义，最严格限制
    """
    BUILTIN = "builtin"    # 内置技能——随 agent-core 发布
    PROJECT = "project"    # 项目级——项目管理员配置
    USER = "user"          # 用户自定义——最低信任


class PromptSkillSource(str, Enum):
    """技能来源标识。"""
    FILE = "file"          # 本地文件加载
    REMOTE = "remote"      # 远程拉取（预留）
    INLINE = "inline"      # 代码内定义


class PromptSkill(BaseModel):
    """Prompt 技能定义——一个可注入到 System Prompt 的 Markdown 知识块。

    Attributes:
        name: 技能名称（唯一标识，如 'drama_writing'）。
        description: 技能描述（一句话概括能力）。
        trust_level: 信任等级（决定安全限制程度）。
        triggers: 触发关键词列表（匹配用户消息时自动注入）。
        content: Markdown 正文（将被注入到 System Prompt）。
        max_tokens: 注入预算上限（防止单个技能过度占用上下文）。
        source: 技能来源。
        file_path: 源文件路径（如果从文件加载）。
        version: 技能版本，用于缓存失效。
        tags: 标签列表（如 ['创意写作', 'AgentCore', '短剧']）。
        requires_context: 是否需要项目上下文（如 VFS 数据）。
        injection_priority: 注入优先级（越小越先注入，越靠前）。
    """
    name: str = Field(description="技能唯一名称")
    description: str = Field(default="", description="技能描述")
    trust_level: SkillTrustLevel = Field(
        default=SkillTrustLevel.USER, description="信任等级"
    )
    triggers: List[str] = Field(
        default_factory=list, description="触发关键词列表"
    )
    content: str = Field(default="", description="Markdown 正文")
    max_tokens: int = Field(
        default=2000, description="注入预算上限（字符数）"
    )
    source: PromptSkillSource = Field(
        default=PromptSkillSource.FILE, description="来源类型"
    )
    file_path: Optional[str] = Field(
        default=None, description="源文件路径"
    )
    version: str = Field(default="1.0", description="技能版本")
    tags: List[str] = Field(
        default_factory=list, description="标签列表"
    )
    requires_context: bool = Field(
        default=False, description="是否需要项目上下文"
    )
    injection_priority: int = Field(
        default=100, description="注入优先级（越小越先）"
    )

    def to_injection_block(self, max_chars: Optional[int] = None) -> str:
        """生成可注入到 System Prompt 的格式化文本块。

        Args:
            max_chars: 覆盖默认的 max_tokens 限制。

        Returns:
            格式化的注入块。
        """
        budget = max_chars or self.max_tokens
        body = self.content[:budget]

        # 如果截断了，添加截断提示
        if len(self.content) > budget:
            body += f"\n\n[技能 {self.name} 内容已截断至 {budget} 字符]"

        return (
            f"<skill name=\"{self.name}\" trust=\"{self.trust_level.value}\">\n"
            f"{body}\n"
            f"</skill>"
        )

    def matches(self, text: str) -> bool:
        """检查给定文本是否触发此技能。"""
        text_lower = text.lower()
        return any(
            trigger.lower() in text_lower
            for trigger in self.triggers
        )


class SkillInjectionResult(BaseModel):
    """技能注入结果摘要。"""
    skills_injected: List[str] = Field(
        default_factory=list, description="已注入的技能名列表"
    )
    total_chars: int = Field(
        default=0, description="注入的总字符数"
    )
    truncated: List[str] = Field(
        default_factory=list, description="被截断的技能名列表"
    )
    blocked: List[str] = Field(
        default_factory=list, description="被安全沙箱拦截的技能"
    )
