"""
Skills 模块 — Skill 自动加载与依赖管理
=======================================

提供 SKILL.md 驱动的技能加载机制：
  - 解析 YAML frontmatter（name, description, requires, install）
  - 自动安装 Python 依赖（pip install）
  - 替换 <skill_dir> 路径占位符
  - 返回可注入 system prompt 的技能上下文
"""

from agent_core.skills.skill_loader import LoadedSkill, load_skill

__all__ = ["LoadedSkill", "load_skill"]
