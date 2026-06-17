"""
Sandbox 会话管理器 — Per-Session 目录隔离
==========================================

借鉴 DeerFlow 2.0 的 Sandbox 虚拟文件系统设计，
为每个 Agent 会话提供独立的工作目录。

功能：
  1. 自动创建 per-session 目录结构：
     /data/sandbox/{session_id}/{workspace, uploads, outputs}
  2. 虚拟路径翻译：Agent 看到 /workspace/...，实际映射到 session 目录
  3. 会话结束后的清理机制
  4. 工作目录大小限制（防止 Agent 写入过多数据）

目录结构::

    /data/sandbox/
    └── {session_id}/
        ├── workspace/    # Agent 工作目录（读写）
        ├── uploads/      # 用户上传的文件（只读）
        └── outputs/      # Agent 输出的文件（读写，下载入口）
"""

import os
import shutil
import time
from pathlib import Path
from typing import Dict, Optional

from loguru import logger


# 默认根目录（Docker 中挂载的卷）
_DEFAULT_SANDBOX_ROOT = os.getenv("SANDBOX_ROOT", "/data/sandbox")

# 每个 session 的最大磁盘占用（字节，默认 100MB）
_MAX_SESSION_SIZE_BYTES = int(os.getenv("SANDBOX_MAX_SIZE", str(100 * 1024 * 1024)))

# session 目录下的标准子目录
_SUBDIRS = ("workspace", "uploads", "outputs")

# 虚拟路径前缀 → 子目录名的映射
_VIRTUAL_PATH_MAP = {
    "/workspace": "workspace",
    "/uploads": "uploads",
    "/outputs": "outputs",
}


class SandboxSessionManager:
    """Per-Session 沙箱目录管理器。

    管理 Agent 会话的隔离目录，提供创建、路径翻译和清理功能。

    Args:
        root: 沙箱根目录路径。
        max_size_bytes: 每个 session 的最大磁盘占用。
    """

    def __init__(
        self,
        root: Optional[str] = None,
        max_size_bytes: int = _MAX_SESSION_SIZE_BYTES,
    ):
        self.root = Path(root or _DEFAULT_SANDBOX_ROOT)
        self.max_size_bytes = max_size_bytes
        # 缓存已创建的 session 路径
        self._sessions: Dict[str, Path] = {}

    def create_session(self, session_id: str) -> Path:
        """创建 session 目录结构。

        若目录已存在，直接返回（幂等）。

        Args:
            session_id: 会话唯一标识。

        Returns:
            session 根目录的 Path 对象。
        """
        session_path = self.root / session_id

        if session_id in self._sessions:
            return self._sessions[session_id]

        # 创建目录结构
        for subdir in _SUBDIRS:
            (session_path / subdir).mkdir(parents=True, exist_ok=True)

        self._sessions[session_id] = session_path
        logger.info(
            f"[SandboxSession] Created session directory: {session_path}"
        )

        return session_path

    def resolve_virtual_path(
        self,
        session_id: str,
        virtual_path: str,
    ) -> Optional[Path]:
        """将 Agent 的虚拟路径翻译为实际文件系统路径。

        虚拟路径格式：/workspace/file.py → {root}/{session_id}/workspace/file.py

        安全检查：
          - 必须以已知虚拟前缀开头
          - 路径不得包含 .. 遍历
          - 最终路径必须在 session 目录内

        Args:
            session_id: 会话 ID。
            virtual_path: Agent 提交的虚拟路径（如 "/workspace/script.py"）。

        Returns:
            解析后的实际 Path，路径非法时返回 None。
        """
        # 路径遍历检查
        if ".." in virtual_path:
            logger.warning(
                f"[SandboxSession] Path traversal attempt: {virtual_path}"
            )
            return None

        # 匹配虚拟前缀
        matched_subdir = None
        relative = ""
        for prefix, subdir in _VIRTUAL_PATH_MAP.items():
            if virtual_path.startswith(prefix):
                matched_subdir = subdir
                relative = virtual_path[len(prefix):].lstrip("/")
                break

        if matched_subdir is None:
            logger.warning(
                f"[SandboxSession] Unknown virtual path prefix: {virtual_path}"
            )
            return None

        session_path = self.root / session_id / matched_subdir / relative
        resolved = session_path.resolve()

        # 确保解析后的路径仍在 session 目录内
        session_root = (self.root / session_id).resolve()
        if not str(resolved).startswith(str(session_root)):
            logger.warning(
                f"[SandboxSession] Path escape attempt: {virtual_path} → {resolved}"
            )
            return None

        return resolved

    def get_workspace_path(self, session_id: str) -> Path:
        """获取 session 的工作目录路径。

        Args:
            session_id: 会话 ID。

        Returns:
            工作目录 Path。
        """
        return self.root / session_id / "workspace"

    def get_outputs_path(self, session_id: str) -> Path:
        """获取 session 的输出目录路径。

        Args:
            session_id: 会话 ID。

        Returns:
            输出目录 Path。
        """
        return self.root / session_id / "outputs"

    def check_size_limit(self, session_id: str) -> bool:
        """检查 session 目录是否超过大小限制。

        Args:
            session_id: 会话 ID。

        Returns:
            True = 在限制内，False = 已超限。
        """
        session_path = self.root / session_id
        if not session_path.exists():
            return True

        total_size = self._get_dir_size(session_path)
        within_limit = total_size <= self.max_size_bytes

        if not within_limit:
            logger.warning(
                f"[SandboxSession] Session {session_id} exceeded size limit: "
                f"{total_size / (1024*1024):.1f}MB > "
                f"{self.max_size_bytes / (1024*1024):.1f}MB"
            )

        return within_limit

    def cleanup_session(self, session_id: str) -> bool:
        """清理 session 目录（会话结束后调用）。

        Args:
            session_id: 会话 ID。

        Returns:
            是否成功清理。
        """
        session_path = self.root / session_id
        self._sessions.pop(session_id, None)

        if not session_path.exists():
            return True

        try:
            shutil.rmtree(session_path)
            logger.info(
                f"[SandboxSession] Cleaned up session: {session_id}"
            )
            return True
        except Exception as e:
            logger.error(
                f"[SandboxSession] Cleanup failed for {session_id}: {e}"
            )
            return False

    def cleanup_expired_sessions(self, max_age_seconds: int = 3600) -> int:
        """清理过期的 session 目录。

        遍历所有 session 目录，删除超过指定时间的旧目录。
        用于定时任务自动回收磁盘空间。

        Args:
            max_age_seconds: 最大存活时间（秒），默认 1 小时。

        Returns:
            清理的 session 数量。
        """
        if not self.root.exists():
            return 0

        now = time.time()
        cleaned = 0

        for session_dir in self.root.iterdir():
            if not session_dir.is_dir():
                continue

            try:
                # 使用目录修改时间判断过期
                mtime = session_dir.stat().st_mtime
                if now - mtime > max_age_seconds:
                    shutil.rmtree(session_dir)
                    self._sessions.pop(session_dir.name, None)
                    cleaned += 1
                    logger.info(
                        f"[SandboxSession] Expired session cleaned: "
                        f"{session_dir.name}"
                    )
            except Exception as e:
                logger.error(
                    f"[SandboxSession] Error checking session "
                    f"{session_dir.name}: {e}"
                )

        if cleaned > 0:
            logger.info(
                f"[SandboxSession] Cleaned {cleaned} expired session(s)"
            )

        return cleaned

    @staticmethod
    def _get_dir_size(path: Path) -> int:
        """递归计算目录总大小（字节）。

        Args:
            path: 目录路径。

        Returns:
            总大小（bytes）。
        """
        total = 0
        try:
            for entry in path.rglob("*"):
                if entry.is_file():
                    total += entry.stat().st_size
        except Exception:
            pass
        return total

    @property
    def active_sessions(self) -> int:
        """当前活跃的 session 数。"""
        return len(self._sessions)


# 模块级单例
sandbox_session_manager = SandboxSessionManager()
