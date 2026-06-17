"""
Skills-as-Markdown 注册器 — SkillRegistry
=============================================

借鉴 DeerFlow 2.0 和 OpenClaw 的 Skills 即 Markdown 机制。
每个 Skill 定义为一个 SKILL.md 文件（含 YAML frontmatter），
Agent 在需要时按需加载 Skill 文档作为上下文。

与 DeerFlow / OpenClaw 的异同：
  - OpenClaw 在 Gateway 启动时扫描 Skill 目录，构建 SkillSummary 列表
  - DeerFlow 使用 Python 函数定义 Skills
  - AgentCore 使用 Markdown 文件定义 Skills，运行时懒加载

目录结构::

    agent-skills/
    ├── code_review/
    │   └── SKILL.md
    ├── story_writing/
    │   └── SKILL.md
    └── data_analysis/
        └── SKILL.md

SKILL.md 格式::

    ---
    name: code_review
    description: 审查代码质量和安全性
    triggers:
      - 代码审查
      - code review
      - 检查代码
    ---

    # Code Review Skill

    你是一个代码审查专家。请按以下步骤审查代码：
    1. 检查代码风格...
    2. 检查安全性...
"""

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from loguru import logger


# 默认 Skills 目录 — 独立于 backend，位于 repo 根目录下的 agent-skills/
_DEFAULT_SKILLS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    "agent-skills",
)


@dataclass
class QualityGate:
    """一个质量门禁定义 (借鉴 superpowers 的 HARD-GATE 模式)。

    Attributes:
        gate: 门禁标识（如 security_check）。
        description: 门禁描述。
        hard_gate: 是否为强制门禁（强制门禁必须通过才能继续）。
    """
    gate: str = ""
    description: str = ""
    hard_gate: bool = False


@dataclass
class SkillDefinition:
    """Skill 定义，从 SKILL.md 文件解析。

    Attributes:
        name: Skill 名称（唯一标识）。
        description: Skill 简短描述。
        triggers: 触发关键词列表（用于匹配用户意图）。
        quality_gates: 质量门禁列表 (借鉴 superpowers HARD-GATE)。
        industry: 所属行业标签 (如 'manufacturing')，用于按行业过滤。
        pack: 所属技能包 ID (如 'manufacturing_qa')，用于按包过滤。
        content: Skill 完整的 Markdown 内容（懒加载）。
        file_path: Skill 文件的绝对路径。
        loaded: 是否已加载完整内容。
    """
    name: str = ""
    description: str = ""
    triggers: List[str] = field(default_factory=list)
    quality_gates: List[QualityGate] = field(default_factory=list)
    industry: str = ""     # SCALE-02: 行业标签
    pack: str = ""         # SCALE-02: 所属技能包 ID
    content: str = ""
    file_path: str = ""
    loaded: bool = False


class SkillRegistry:
    """Skills-as-Markdown 注册和管理器。

    负责扫描 skills 目录、解析 SKILL.md frontmatter、
    按需加载 Skill 内容、以及根据用户意图匹配 Skill。

    Args:
        skills_dir: Skills 目录路径。
    """

    def __init__(self, skills_dir: Optional[str] = None):
        self.skills_dir = Path(skills_dir or _DEFAULT_SKILLS_DIR)
        # name → SkillDefinition
        self._skills: Dict[str, SkillDefinition] = {}
        self._scanned = False

    def scan(self) -> int:
        """扫描 skills 目录，解析所有 SKILL.md frontmatter。

        仅解析 YAML frontmatter（name/description/triggers），
        不加载完整 Markdown 内容（懒加载策略）。

        Returns:
            发现的 Skill 数量。
        """
        if not self.skills_dir.exists():
            logger.info(
                f"[SkillRegistry] Skills directory not found: "
                f"{self.skills_dir}, creating..."
            )
            self.skills_dir.mkdir(parents=True, exist_ok=True)
            return 0

        count = 0
        for skill_dir in self.skills_dir.iterdir():
            if not skill_dir.is_dir():
                continue

            skill_file = skill_dir / "SKILL.md"
            if not skill_file.exists():
                continue

            try:
                definition = self._parse_frontmatter(skill_file)
                if definition and definition.name:
                    self._skills[definition.name] = definition
                    count += 1
                    logger.debug(
                        f"[SkillRegistry] Registered: {definition.name} "
                        f"({len(definition.triggers)} triggers)"
                    )
            except Exception as e:
                logger.error(
                    f"[SkillRegistry] Failed to parse {skill_file}: {e}"
                )

        self._scanned = True
        logger.info(
            f"[SkillRegistry] Scan complete: {count} skill(s) found "
            f"in {self.skills_dir}"
        )
        return count

    def get_skill(self, name: str) -> Optional[SkillDefinition]:
        """按名称获取 Skill 定义（触发懒加载）。

        首次访问时加载完整 Markdown 内容。

        Args:
            name: Skill 名称。

        Returns:
            SkillDefinition 或 None。
        """
        if not self._scanned:
            self.scan()

        skill = self._skills.get(name)
        if skill and not skill.loaded:
            self._load_content(skill)

        return skill

    def match_skills(
        self,
        user_message: str,
        max_matches: int = 3,
    ) -> List[SkillDefinition]:
        """根据用户消息匹配相关 Skills。

        使用简单的关键词匹配策略：检查用户消息是否包含
        Skill 的任何 trigger 关键词。

        Args:
            user_message: 用户消息文本。
            max_matches: 最多返回的匹配数。

        Returns:
            匹配的 SkillDefinition 列表（已懒加载内容）。
        """
        if not self._scanned:
            self.scan()

        msg_lower = user_message.lower()
        matches = []

        for name, skill in self._skills.items():
            for trigger in skill.triggers:
                if trigger.lower() in msg_lower:
                    # 触发匹配，懒加载内容
                    if not skill.loaded:
                        self._load_content(skill)
                    matches.append(skill)
                    break

            if len(matches) >= max_matches:
                break

        return matches

    def get_all_summaries(self) -> List[Dict[str, Any]]:
        """获取所有已注册 Skill 的摘要信息（含质量门禁）。

        不触发内容懒加载，仅返回 frontmatter 信息。
        用于构建 Agent 的 system prompt 中的 Skill 列表。

        Returns:
            Skill 摘要字典列表。
        """
        if not self._scanned:
            self.scan()

        return [
            {
                "name": s.name,
                "description": s.description,
                "triggers": s.triggers,
                "quality_gates": [
                    {
                        "gate": g.gate,
                        "description": g.description,
                        "hard_gate": g.hard_gate,
                    }
                    for g in s.quality_gates
                ],
                "has_hard_gates": any(g.hard_gate for g in s.quality_gates),
            }
            for s in self._skills.values()
        ]

    def get_skills_by_industry(self, industry: str) -> List[SkillDefinition]:
        """按行业标签筛选技能。

        Args:
            industry: 行业标签 (如 'manufacturing')。

        Returns:
            匹配行业的 SkillDefinition 列表。
        """
        if not self._scanned:
            self.scan()
        return [
            s for s in self._skills.values()
            if s.industry and s.industry.lower() == industry.lower()
        ]

    def get_skills_by_pack(self, pack_id: str) -> List[SkillDefinition]:
        """按技能包 ID 筛选技能。

        Args:
            pack_id: 技能包 ID (如 'manufacturing_qa')。

        Returns:
            匹配技能包的 SkillDefinition 列表。
        """
        if not self._scanned:
            self.scan()
        return [
            s for s in self._skills.values()
            if s.pack and s.pack == pack_id
        ]

    def _parse_frontmatter(self, file_path: Path) -> Optional[SkillDefinition]:
        """解析 SKILL.md 的 YAML frontmatter。

        frontmatter 格式::

            ---
            name: skill_name
            description: Skill 描述
            triggers:
              - 关键词1
              - 关键词2
            ---

        Args:
            file_path: SKILL.md 文件路径。

        Returns:
            解析后的 SkillDefinition（不含完整内容）。
        """
        text = file_path.read_text(encoding="utf-8")

        # 提取 frontmatter
        fm_match = re.match(r"^---\s*\n(.*?)\n---", text, re.DOTALL)
        if not fm_match:
            logger.warning(
                f"[SkillRegistry] No frontmatter in {file_path}"
            )
            return None

        try:
            fm = yaml.safe_load(fm_match.group(1))
        except yaml.YAMLError as e:
            logger.error(
                f"[SkillRegistry] Invalid YAML in {file_path}: {e}"
            )
            return None

        if not isinstance(fm, dict):
            return None

        # 解析质量门禁 (借鉴 superpowers HARD-GATE)
        raw_gates = fm.get("quality_gates", [])
        gates = []
        if isinstance(raw_gates, list):
            for g in raw_gates:
                if isinstance(g, dict):
                    gates.append(QualityGate(
                        gate=g.get("gate", ""),
                        description=g.get("description", ""),
                        hard_gate=bool(g.get("hard_gate", False)),
                    ))

        return SkillDefinition(
            name=fm.get("name", ""),
            description=fm.get("description", ""),
            triggers=fm.get("triggers", []),
            quality_gates=gates,
            industry=fm.get("industry", ""),
            pack=fm.get("pack", ""),
            file_path=str(file_path),
            loaded=False,
        )

    def _load_content(self, skill: SkillDefinition) -> None:
        """懒加载 Skill 的完整 Markdown 内容。

        读取 SKILL.md 文件，去掉 frontmatter 后保留正文。

        Args:
            skill: 要加载内容的 Skill 定义。
        """
        try:
            text = Path(skill.file_path).read_text(encoding="utf-8")

            # 去掉 frontmatter
            content = re.sub(r"^---\s*\n.*?\n---\s*\n?", "", text, flags=re.DOTALL)
            skill.content = content.strip()
            skill.loaded = True

            logger.debug(
                f"[SkillRegistry] Loaded content for '{skill.name}': "
                f"{len(skill.content)} chars"
            )
        except Exception as e:
            logger.error(
                f"[SkillRegistry] Failed to load {skill.file_path}: {e}"
            )

    @property
    def skill_count(self) -> int:
        """已注册的 Skill 数量。"""
        return len(self._skills)


# ---------------------------------------------------------------------------
# 全局单例 + 异步适配器（供 SkillInjectionMiddleware 使用）
# ---------------------------------------------------------------------------

# 全局单例，在首次访问时懒加载
skill_registry = SkillRegistry()


async def get_summaries_for_agent(
    agent_id: int,
    tenant_id: str = "default",
    skill_pack_ids: Optional[List[str]] = None,
) -> list:
    """异步适配器 — 供 SkillInjectionMiddleware 的 skill_loader 回调使用。

    支持按租户关联的技能包过滤返回的 Skills。
    当 skill_pack_ids 为空时返回所有通用 Skills（无 pack 标签的）。

    Args:
        agent_id: Agent ID（预留参数，当前未使用）。
        tenant_id: 租户 ID，用于日志追踪。
        skill_pack_ids: 租户已开通的技能包 ID 列表。

    Returns:
        Skill 摘要列表，每项含 name/description/capability_summary。
    """
    if not skill_registry._scanned:
        skill_registry.scan()

    summaries = skill_registry.get_all_summaries()

    # SCALE-02: 按技能包过滤
    if skill_pack_ids:
        # 返回: 通用技能 (无 pack 标签) + 租户已开通 pack 的技能
        pack_set = set(skill_pack_ids)
        filtered = []
        for s in summaries:
            skill_def = skill_registry._skills.get(s["name"])
            if skill_def and skill_def.pack:
                if skill_def.pack in pack_set:
                    filtered.append(s)
            else:
                # 无 pack 标签 = 通用技能，所有租户可见
                filtered.append(s)
        summaries = filtered

    # 将 triggers + quality_gates 转化为 capability_summary
    result = []
    for s in summaries:
        triggers_str = ", ".join(s.get("triggers", []))
        # 包含 HARD-GATE 信息
        gates = s.get("quality_gates", [])
        hard_gates = [g for g in gates if g.get("hard_gate")]
        gates_str = ""
        if hard_gates:
            gate_names = [g["gate"] for g in hard_gates]
            gates_str = f" | HARD-GATE: {', '.join(gate_names)}"

        result.append({
            "name": s["name"],
            "description": s["description"],
            "capability_summary": f"{triggers_str}{gates_str}",
        })
    return result


