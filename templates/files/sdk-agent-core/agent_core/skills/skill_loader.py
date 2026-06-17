"""
Skill Loader — SKILL.md 驱动的技能自动加载器
=============================================

基于 OpenClaw 的 Skill 架构设计，提供：
  - YAML frontmatter 解析（name, description, requires）
  - 自动探测 package.json / requirements.txt（兼容 ljg-skills 等外部生态）
  - Docker Named Volume 动态依赖检测与按需安装
  - <skill_dir> 路径占位符替换为容器内路径
  - 多 Skill 批量加载

依赖管理策略（Docker Named Volume）：
  - 检测：在临时容器中挂载 Volume 后 import/require 测试
  - 安装：启动有网临时容器，pip install -t / npm install --prefix 到 Volume
  - 运行时：sandbox_executor 将 Volume 以 :ro 只读挂载 + PYTHONPATH/NODE_PATH
  - 此方案独立于后端容器生死周期，完美适配兄弟容器 (Sibling Containers) 模式
"""

import asyncio
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger


# Docker 沙箱镜像名（与 sandbox_executor 共用同一配置）
_DOCKER_IMAGE = os.getenv("SANDBOX_DOCKER_IMAGE", "agent-sandbox:latest")

# Docker Named Volume — 持久化沙箱依赖
# 独立于任何容器（后端容器挂了也不丢失）
SANDBOX_DEPS_VOLUME = os.getenv("SANDBOX_DEPS_VOLUME", "agent_sandbox_deps")


@dataclass
class LoadedSkill:
    """加载完成的 Skill。

    Attributes:
        name: Skill 名称（来自 YAML frontmatter）。
        description: Skill 描述（来自 YAML frontmatter）。
        skill_dir: Skill 根目录绝对路径（宿主机路径，用于挂载到容器）。
        container_skill_dir: 容器内 skill 目录路径（如 /skills/write）。
        system_prompt: SKILL.md body，<skill_dir> 已替换为容器内路径。
        python_packages: 声明的 Python 依赖列表。
        node_packages: 声明的 Node.js 依赖列表。
        deps_installed: 所有依赖是否已在 Named Volume 中安装。
        raw_frontmatter: 原始 YAML frontmatter 字典。
    """
    name: str
    description: str
    skill_dir: Path
    container_skill_dir: str
    system_prompt: str
    python_packages: List[str] = field(default_factory=list)
    node_packages: List[str] = field(default_factory=list)
    deps_installed: bool = False
    raw_frontmatter: Dict[str, Any] = field(default_factory=dict)


# ──────────────────────────────────────────────────────────────────────
# SKILL.md 解析
# ──────────────────────────────────────────────────────────────────────

def _parse_skill_md(content: str) -> tuple:
    """解析 SKILL.md 的 YAML frontmatter 和 body。

    YAML frontmatter 格式：
    ---
    name: xxx
    description: |
      xxx
    requires:
      python_packages:
        - python-docx
      node_packages:
        - playwright
    ---
    (body content)

    Args:
        content: SKILL.md 完整文件内容。

    Returns:
        (frontmatter_dict, body_text) 元组。
        若无 frontmatter，返回空字典和完整内容。
    """
    import yaml

    content = content.strip()
    if not content.startswith("---"):
        return {}, content

    end_idx = content.find("---", 3)
    if end_idx == -1:
        return {}, content

    fm_text = content[3:end_idx].strip()
    body = content[end_idx + 3:].strip()

    try:
        fm = yaml.safe_load(fm_text) or {}
    except Exception as e:
        logger.warning(f"[SkillLoader] YAML frontmatter 解析失败: {e}")
        fm = {}

    return fm, body


def _detect_extra_deps(skill_dir: Path) -> tuple:
    """自动探测 Skill 目录中的标准包管理文件。

    为兼容 ljg-skills 等外部生态，自动识别：
    - package.json → 提取 dependencies 键
    - requirements.txt → 逐行读取包名

    Args:
        skill_dir: Skill 根目录路径。

    Returns:
        (python_packages, node_packages) 额外发现的依赖列表。
    """
    extra_py: List[str] = []
    extra_node: List[str] = []

    # 探测 package.json（ljg-card 等 Node.js skills）
    pkg_json = skill_dir / "package.json"
    if pkg_json.exists():
        try:
            data = json.loads(pkg_json.read_text(encoding="utf-8"))
            deps = data.get("dependencies", {})
            extra_node.extend(deps.keys())
            logger.info(
                f"[SkillLoader] 探测到 package.json: "
                f"{list(deps.keys())}"
            )
        except Exception as e:
            logger.warning(f"[SkillLoader] package.json 解析失败: {e}")

    # 探测 requirements.txt
    req_txt = skill_dir / "requirements.txt"
    if req_txt.exists():
        try:
            lines = req_txt.read_text(encoding="utf-8").splitlines()
            for line in lines:
                line = line.strip()
                # 跳过注释和空行
                if line and not line.startswith("#"):
                    # 取包名（去掉版本约束）
                    pkg = line.split("==")[0].split(">=")[0].split("<=")[0].strip()
                    if pkg:
                        extra_py.append(pkg)
            logger.info(
                f"[SkillLoader] 探测到 requirements.txt: {extra_py}"
            )
        except Exception as e:
            logger.warning(f"[SkillLoader] requirements.txt 解析失败: {e}")

    return extra_py, extra_node


# ──────────────────────────────────────────────────────────────────────
# 依赖检测与安装（Docker Named Volume）
# ──────────────────────────────────────────────────────────────────────

async def _run_docker(
    args: List[str],
    timeout: int = 30,
    network: str = "none",
) -> int:
    """封装 docker run 调用，返回退出码。

    Args:
        args: docker run 后面的参数列表。
        timeout: 超时秒数。
        network: 网络模式（检测用 none，安装用 bridge）。

    Returns:
        进程退出码。
    """
    cmd = [
        "docker", "run", "--rm",
        f"--network={network}",
        *args,
    ]
    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(
            process.communicate(), timeout=timeout,
        )
        if process.returncode != 0 and stderr:
            logger.debug(
                f"[SkillLoader] docker stderr: "
                f"{stderr.decode('utf-8', errors='replace')[:300]}"
            )
        return process.returncode or 0
    except asyncio.TimeoutError:
        logger.error(f"[SkillLoader] docker 命令超时({timeout}s)")
        return -1
    except FileNotFoundError:
        logger.error("[SkillLoader] docker 命令未找到")
        return -1


async def _ensure_python_deps(packages: List[str]) -> bool:
    """检测并按需安装 Python 依赖到 Named Volume。

    检测：在 Volume 中 import 测试
    安装：pip install -t /sandbox_deps/python

    Args:
        packages: Python 包名列表。

    Returns:
        True 表示所有依赖已就绪。
    """
    if not packages:
        return True

    # 构造 import 检测脚本
    # pip 包名和 import 名可能不同（如 python-docx → docx），需要做映射
    import_map = {
        "python-docx": "docx",
        "beautifulsoup4": "bs4",
        "Pillow": "PIL",
        "scikit-learn": "sklearn",
        "opencv-python": "cv2",
    }
    import_names = [import_map.get(p, p.replace("-", "_")) for p in packages]
    check_script = (
        "import sys; sys.path.insert(0, '/sandbox_deps/python'); "
        + "; ".join(f"import {n}" for n in import_names)
    )

    # 检测（无网络）
    rc = await _run_docker([
        "-v", f"{SANDBOX_DEPS_VOLUME}:/sandbox_deps",
        _DOCKER_IMAGE,
        "python", "-c", check_script,
    ], timeout=15)

    if rc == 0:
        for pkg in packages:
            logger.debug(f"[SkillLoader] Python 依赖已就绪: {pkg}")
        return True

    # 检测失败 → 安装（需要网络）
    logger.info(
        f"[SkillLoader] Python 依赖缺失，正在安装到 Named Volume: "
        f"{packages}"
    )
    install_rc = await _run_docker([
        "-v", f"{SANDBOX_DEPS_VOLUME}:/sandbox_deps",
        _DOCKER_IMAGE,
        "pip", "install", "--no-cache-dir",
        "-t", "/sandbox_deps/python",
        *packages,
    ], timeout=120, network="bridge")

    if install_rc == 0:
        logger.info(f"[SkillLoader] Python 依赖安装成功: {packages}")
        return True
    else:
        logger.error(f"[SkillLoader] Python 依赖安装失败: {packages}")
        return False


async def _ensure_node_deps(packages: List[str]) -> bool:
    """检测并按需安装 Node.js 依赖到 Named Volume。

    检测：在 Volume 中 require 测试
    安装：npm install --prefix /sandbox_deps/node

    Args:
        packages: Node.js 包名列表。

    Returns:
        True 表示所有依赖已就绪。
    """
    if not packages:
        return True

    # 构造 require 检测脚本
    require_checks = "; ".join(
        f"require('{pkg}')" for pkg in packages
    )
    check_script = (
        f"module.paths.push('/sandbox_deps/node/node_modules'); "
        f"{require_checks}"
    )

    # 检测（无网络）
    rc = await _run_docker([
        "-v", f"{SANDBOX_DEPS_VOLUME}:/sandbox_deps",
        _DOCKER_IMAGE,
        "node", "-e", check_script,
    ], timeout=15)

    if rc == 0:
        for pkg in packages:
            logger.debug(f"[SkillLoader] Node 依赖已就绪: {pkg}")
        return True

    # 检测失败 → 安装（需要网络）
    logger.info(
        f"[SkillLoader] Node 依赖缺失，正在安装到 Named Volume: "
        f"{packages}"
    )
    install_rc = await _run_docker([
        "-v", f"{SANDBOX_DEPS_VOLUME}:/sandbox_deps",
        _DOCKER_IMAGE,
        "npm", "install", "--prefix", "/sandbox_deps/node",
        *packages,
    ], timeout=120, network="bridge")

    if install_rc == 0:
        logger.info(f"[SkillLoader] Node 依赖安装成功: {packages}")
        return True
    else:
        logger.error(f"[SkillLoader] Node 依赖安装失败: {packages}")
        return False


# ──────────────────────────────────────────────────────────────────────
# 核心加载逻辑
# ──────────────────────────────────────────────────────────────────────

async def load_skill(skill_dir: Path) -> LoadedSkill:
    """加载 Skill：解析 SKILL.md → 探测额外依赖 → 动态安装 → 返回 LoadedSkill。

    流程：
    1. 读取 <skill_dir>/SKILL.md
    2. 解析 YAML frontmatter（name, description, requires）
    3. 从 requires 提取 python_packages / node_packages
    4. 自动探测 package.json / requirements.txt（兼容外部生态）
    5. 在 Docker Named Volume 中检测与按需安装依赖
    6. 将 body 中 <skill_dir> 替换为容器内路径
    7. 返回 LoadedSkill

    Args:
        skill_dir: Skill 根目录路径。

    Returns:
        LoadedSkill 实例。

    Raises:
        FileNotFoundError: SKILL.md 不存在。
    """
    skill_dir = skill_dir.resolve()
    skill_md = skill_dir / "SKILL.md"

    if not skill_md.exists():
        raise FileNotFoundError(f"SKILL.md 不存在: {skill_md}")

    content = skill_md.read_text(encoding="utf-8")
    fm, body = _parse_skill_md(content)

    name = fm.get("name", skill_dir.name)
    description = fm.get("description", "").strip()

    # ── 从 frontmatter 提取依赖 ────────────────────────────────────
    requires = fm.get("requires", {})
    python_packages: List[str] = []
    node_packages: List[str] = []

    if isinstance(requires, dict):
        python_packages = requires.get("python_packages", [])
        node_packages = requires.get("node_packages", [])
    elif isinstance(requires, list):
        # 兼容简写格式: requires: [python-docx]
        python_packages = requires

    # ── 自动探测标准包管理文件 ──────────────────────────────────────
    # 兼容 ljg-skills（package.json）等外部生态
    extra_py, extra_node = _detect_extra_deps(skill_dir)
    python_packages = list(dict.fromkeys(python_packages + extra_py))
    node_packages = list(dict.fromkeys(node_packages + extra_node))

    # ── 在 Docker Named Volume 中检测与按需安装 ───────────────────
    py_ok = await _ensure_python_deps(python_packages)
    node_ok = await _ensure_node_deps(node_packages)
    deps_ok = py_ok and node_ok

    # ── 替换路径占位符 ─────────────────────────────────────────────
    # <skill_dir> → 容器内路径（/skills/{name}）
    # 代码在 Docker 容器内执行，宿主机路径不可用
    container_dir = f"/skills/{name}"
    resolved_body = body.replace("<skill_dir>", container_dir)

    all_deps = python_packages + node_packages
    logger.info(
        f"[SkillLoader] Skill '{name}' 加载完成 | "
        f"body={len(resolved_body)} chars | "
        f"py={python_packages} node={node_packages} | "
        f"installed={deps_ok} | container_dir={container_dir}"
    )

    return LoadedSkill(
        name=name,
        description=description,
        skill_dir=skill_dir,
        container_skill_dir=container_dir,
        system_prompt=resolved_body,
        python_packages=python_packages,
        node_packages=node_packages,
        deps_installed=deps_ok,
        raw_frontmatter=fm,
    )


async def load_skills(skill_dirs: List[Path]) -> List[LoadedSkill]:
    """批量加载多个 Skill。

    Args:
        skill_dirs: Skill 目录列表。

    Returns:
        LoadedSkill 列表（加载失败的跳过并记录警告）。
    """
    skills = []
    for d in skill_dirs:
        try:
            skill = await load_skill(d)
            skills.append(skill)
        except Exception as e:
            logger.warning(f"[SkillLoader] 跳过 Skill '{d.name}': {e}")
    return skills
