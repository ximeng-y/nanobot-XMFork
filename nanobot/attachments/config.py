"""附件存储策略配置。"""

from __future__ import annotations

from pydantic import Field, field_validator

from nanobot.config_base import Base


class AttachmentStorageConfig(Base):
    """实例级长期附件容量与自动清理策略。"""

    max_bytes: int | None = Field(default=None, ge=1)
    auto_cleanup: bool = False
    orphan_days: int = Field(default=30, ge=1)

    @field_validator("max_bytes", "orphan_days", mode="before")
    @classmethod
    def reject_boolean_numbers(cls, value: object) -> object:
        if isinstance(value, bool):
            raise ValueError("must be a positive integer")
        return value
