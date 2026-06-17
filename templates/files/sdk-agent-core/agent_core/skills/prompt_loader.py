"""
Prompt 技能热插拔加载器 — PromptSkillLoader
=============================================

遵循三级目录扫描策略加载 Prompt 技能：
  1. _builtin/   — 内置技能（随 agent-core 发布，trust=BUILTIN）
  2. _project/{pid}/ — 项目级技能（项目管理员配置，trust=PROJECT）
  3. _user/{uid}/    — 用户自定义（用户上传，trust=USER）

文件格式与现有 SkillRegistry 兼容（YAML frontmatter + Markdown body），
但增加了 trust_level / max_tokens / tags 等 Prompt 技能专用字段。

与 skill_loader.py 的分工：
  - skill_loader.py → 沙箱代码技能（Docker 执行）
  - prompt_loader.py（本模块）→ Prompt 知识技能（System Prompt 注入）
"""

import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from loguru import logger

from agent_core.skills.prompt_skill import (
    PromptSkill,
    PromptSkillSource,
    SkillTrustLevel,
)


class PromptSkillLoader:
    """Prompt 技能热插拔加载器——三级目录扫描 + YAML 解析。

    Args:
        registry_dir: 技能注册目录根路径。
            默认为 agent_core/skills/registry/
    """

    def __init__(
        self,
        registry_dir: Optional[str] = None,
    ) -> None:
        if registry_dir:
            self._root = Path(registry_dir)
        else:
            self._root = Path(__file__).parent / "registry"

        # 已加载的技能：name → PromptSkill
        self._skills: Dict[str, PromptSkill] = {}

    def scan_all(
        self,
        project_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> int:
        """扫描所有三级目录，加载 Prompt 技能。

        扫描顺序：_builtin → _project/{pid} → _user/{uid}
        同名技能高优先级覆盖低优先级（user > project > builtin）。

        Args:
            project_id: 项目 ID（加载项目级技能）。
            user_id: 用户 ID（加载用户自定义技能）。

        Returns:
            加载的技能总数。
        """
        self._skills.clear()
        total = 0

        # L1: 内置技能
        builtin_dir = self._root / "_builtin"
        total += self._scan_directory(
            builtin_dir, SkillTrustLevel.BUILTIN
        )

        # L2: 项目级技能
        if project_id:
            project_dir = self._root / "_project" / project_id
            total += self._scan_directory(
                project_dir, SkillTrustLevel.PROJECT
            )

        # L3: 用户自定义技能
        if user_id:
            user_dir = self._root / "_user" / user_id
            total += self._scan_directory(
                user_dir, SkillTrustLevel.USER
            )

        logger.info(
            f"[PromptLoader] 扫描完成: {total} 个 Prompt 技能 "
            f"(builtin + project={project_id} + user={user_id})"
        )
        return total

    def _scan_directory(
        self,
        directory: Path,
        trust_level: SkillTrustLevel,
    ) -> int:
        """扫描单个目录下的所有 SKILL.md 文件。"""
        if not directory.exists():
            return 0

        count = 0
        for entry in directory.iterdir():
            skill_file = None
            if entry.is_dir():
                # 子目录模式：dir/SKILL.md
                candidate = entry / "SKILL.md"
                if candidate.exists():
                    skill_file = candidate
            elif entry.suffix == ".md" and entry.name != "README.md":
                # 单文件模式：xxx.md（自动作为 SKILL.md）
                skill_file = entry

            if not skill_file:
                continue

            try:
                skill = self._parse_skill_file(skill_file, trust_level)
                if skill:
                    self._skills[skill.name] = skill
                    count += 1
                    logger.debug(
                        f"[PromptLoader] 加载: {skill.name} "
                        f"trust={trust_level.value} "
                        f"triggers={skill.triggers}"
                    )
            except Exception as e:
                logger.error(
                    f"[PromptLoader] 解析失败: {skill_file} | {e}"
                )

        return count

    def _parse_skill_file(
        self,
        file_path: Path,
        trust_level: SkillTrustLevel,
    ) -> Optional[PromptSkill]:
        """解析 SKILL.md 文件为 PromptSkill 对象。"""
        text = file_path.read_text(encoding="utf-8")

        # 提取 YAML frontmatter
        fm_match = re.match(r"^---\s*\n(.*?)\n---", text, re.DOTALL)
        if not fm_match:
            logger.warning(
                f"[PromptLoader] 无 frontmatter: {file_path}"
            )
            return None

        try:
            fm = yaml.safe_load(fm_match.group(1))
        except yaml.YAMLError as e:
            logger.error(f"[PromptLoader] YAML 解析错误: {e}")
            return None

        if not isinstance(fm, dict) or "name" not in fm:
            return None

        # 提取 body（去掉 frontmatter）
        body = re.sub(
            r"^---\s*\n.*?\n---\s*\n?", "", text, flags=re.DOTALL
        ).strip()

        return PromptSkill(
            name=fm["name"],
            description=fm.get("description", ""),
            trust_level=trust_level,
            triggers=fm.get("triggers", []),
            content=body,
            max_tokens=fm.get("max_tokens", 2000),
            source=PromptSkillSource.FILE,
            file_path=str(file_path),
            version=fm.get("version", "1.0"),
            tags=fm.get("tags", []),
            requires_context=fm.get("requires_context", False),
            injection_priority=fm.get("priority", 100),
        )

    def match_skills(
        self,
        user_message: str,
        max_matches: int = 3,
    ) -> List[PromptSkill]:
        """根据用户消息匹配触发的 Prompt 技能。

        按 injection_priority 排序后返回。

        Args:
            user_message: 用户消息。
            max_matches: 最多返回数量。

        Returns:
            匹配的 PromptSkill 列表。
        """
        matched = [
            skill for skill in self._skills.values()
            if skill.matches(user_message)
        ]

        # 按注入优先级排序
        matched.sort(key=lambda s: s.injection_priority)
        return matched[:max_matches]

    def get_skill(self, name: str) -> Optional[PromptSkill]:
        """按名称获取 Prompt 技能。"""
        return self._skills.get(name)

    def get_all_summaries(self) -> List[Dict[str, Any]]:
        """获取所有技能的摘要信息（不含完整 content）。"""
        return [
            {
                "name": s.name,
                "description": s.description,
                "trust_level": s.trust_level.value,
                "triggers": s.triggers,
                "tags": s.tags,
                "priority": s.injection_priority,
            }
            for s in sorted(
                self._skills.values(),
                key=lambda s: s.injection_priority,
            )
        ]

    @property
    def count(self) -> int:
        """已加载的技能数。"""
        return len(self._skills)
