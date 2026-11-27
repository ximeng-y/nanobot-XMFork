"""向运行时组件提供共享的附件服务入口。"""

from __future__ import annotations

from pathlib import Path

from nanobot.attachments.service import AttachmentService, AttachmentServiceRegistry
from nanobot.config.paths import get_data_dir, get_runtime_subdir
from nanobot.config.schema import AttachmentStorageConfig

_REGISTRY = AttachmentServiceRegistry()


def get_attachment_service(
    *,
    data_dir: Path | None = None,
    sessions_root: Path | None = None,
    config: AttachmentStorageConfig | None = None,
) -> AttachmentService:
    root = (data_dir or get_data_dir()).expanduser().resolve(strict=False)
    sessions = (
        sessions_root.expanduser().resolve(strict=False)
        if sessions_root is not None
        else get_runtime_subdir("sessions").resolve(strict=False)
    )
    existing = _REGISTRY.existing(root)
    if existing is not None and config is None:
        existing.sessions_root = sessions
        return existing
    policy = config or AttachmentStorageConfig()
    return _REGISTRY.get(
        data_dir=root,
        sessions_root=sessions,
        max_bytes=policy.max_bytes,
        auto_cleanup=policy.auto_cleanup,
        orphan_days=policy.orphan_days,
    )


def existing_attachment_service(data_dir: Path | None = None) -> AttachmentService | None:
    root = (data_dir or get_data_dir()).expanduser().resolve(strict=False)
    return _REGISTRY.existing(root)


def clear_attachment_services() -> None:
    """测试隔离入口；生产进程不主动清空共享实例服务。"""
    _REGISTRY.clear()
