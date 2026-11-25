from dataclasses import dataclass


DEFAULT_MAX_COUNT = 4
DEFAULT_MAX_FILE_BYTES = 6 * 1024 * 1024
DEFAULT_MAX_TOTAL_BYTES = 24 * 1024 * 1024
DEFAULT_MAX_VIDEO_COUNT = 1
DEFAULT_MAX_VIDEO_BYTES = 20 * 1024 * 1024
DEFAULT_MAX_PENDING = 128
DEFAULT_MAX_PENDING_BYTES = 128 * 1024 * 1024


@dataclass(frozen=True)
class AttachmentIngressLimits:
    max_count: int = DEFAULT_MAX_COUNT
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES
    max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES
    max_video_count: int = DEFAULT_MAX_VIDEO_COUNT
    max_video_bytes: int = DEFAULT_MAX_VIDEO_BYTES

    @property
    def max_attachments(self) -> int:
        return self.max_count + self.max_video_count

# A 20 MiB upload takes about 168 seconds at 1 Mbps. Keep a finite total
# budget with room for contention, but release stalled uploads promptly.
UPLOAD_TIMEOUT_SECONDS = 300.0
UPLOAD_IDLE_TIMEOUT_SECONDS = 30.0
UPLOAD_REQUEST_TIMEOUT_SECONDS = 315.0

IMAGE_MIME_ALLOWED: frozenset[str] = frozenset({
    "image/png", "image/jpeg", "image/webp", "image/gif",
})
VIDEO_MIME_ALLOWED: frozenset[str] = frozenset({
    "video/mp4", "video/webm", "video/quicktime",
})
DOCUMENT_MIME_ALLOWED: frozenset[str] = frozenset({
    "application/json",
    "application/pdf",
    "application/toml",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/x-yaml",
    "application/xhtml+xml",
    "application/xml",
    "application/yaml",
    "text/csv", "text/html", "text/markdown", "text/plain", "text/xml", "text/yaml",
})
UPLOAD_MIME_ALLOWED = IMAGE_MIME_ALLOWED | VIDEO_MIME_ALLOWED | DOCUMENT_MIME_ALLOWED


def validate_attachment_batch(
    mimes: list[str], *, limits: AttachmentIngressLimits,
) -> None:
    """Apply shared format and count limits without imposing an upload budget."""
    videos = sum(mime in VIDEO_MIME_ALLOWED for mime in mimes)
    if videos > limits.max_video_count:
        raise ValueError(f"Too many video attachments (maximum {limits.max_video_count})")
    if len(mimes) - videos > limits.max_count:
        raise ValueError(f"Too many non-video attachments (maximum {limits.max_count})")
    if any(mime not in UPLOAD_MIME_ALLOWED for mime in mimes):
        raise ValueError("Unsupported attachment MIME")


def validate_upload_size(mime: str, size: int, *, limits: AttachmentIngressLimits) -> None:
    if isinstance(size, bool) or size <= 0:
        raise ValueError("Empty files cannot be uploaded")
    limit = limits.max_video_bytes if mime in VIDEO_MIME_ALLOWED else limits.max_file_bytes
    if size > limit:
        maximum = f"{limit / (1024 * 1024):g} MiB" if limit >= 1024 * 1024 else f"{limit:,} bytes"
        raise ValueError(f"Attachment exceeds the {maximum} upload limit ({size:,} bytes)")


def validate_upload_batch(
    items: list[tuple[str, int]], *, limits: AttachmentIngressLimits,
) -> None:
    """Upload ownership alone incurs per-file and cumulative byte limits."""
    validate_attachment_batch([mime for mime, _ in items], limits=limits)
    for mime, size in items:
        validate_upload_size(mime, size, limits=limits)
    total = sum(size for mime, size in items if mime not in VIDEO_MIME_ALLOWED)
    if total > limits.max_total_bytes:
        raise ValueError(f"Uploaded attachments exceed the total upload limit ({total:,} bytes; maximum {limits.max_total_bytes:,} bytes)")
