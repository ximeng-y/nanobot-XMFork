"""托管附件的领域模型与持久化接口。"""

from nanobot.attachments.config import AttachmentStorageConfig
from nanobot.attachments.service import AttachmentPolicy, AttachmentService
from nanobot.attachments.store import (
    AttachmentCapacityError,
    AttachmentDeleteResult,
    AttachmentDeletePreview,
    AttachmentFile,
    AttachmentRecord,
    AttachmentReference,
    AttachmentStore,
    CapacityReservation,
)

__all__ = [
    "AttachmentCapacityError",
    "AttachmentDeletePreview",
    "AttachmentDeleteResult",
    "AttachmentFile",
    "AttachmentPolicy",
    "AttachmentRecord",
    "AttachmentReference",
    "AttachmentService",
    "AttachmentStorageConfig",
    "AttachmentStore",
    "CapacityReservation",
]
