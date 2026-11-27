"""Bounded staging for binary uploads, independent of WebSocket frame limits.

The HTTP adapter must authenticate before calling ``upload``. ``owner`` must be
server-authenticated identity, never a client-selected ID. References are only
transport state; callers persist the paths returned by ``resolve`` as before.
"""

from __future__ import annotations

import asyncio
import secrets
import time
from collections.abc import AsyncIterable
from dataclasses import dataclass
from pathlib import Path

from nanobot.attachments.store import AttachmentCapacityError, CapacityReservation
from nanobot.attachments.store import AttachmentStore as ManagedAttachmentStore
from nanobot.channels.websocket.attachment_policy import (
    DEFAULT_MAX_PENDING,
    DEFAULT_MAX_PENDING_BYTES,
    DOCUMENT_MIME_ALLOWED,
    UPLOAD_IDLE_TIMEOUT_SECONDS,
    UPLOAD_MIME_ALLOWED,
    UPLOAD_TIMEOUT_SECONDS,
    AttachmentIngressLimits,
    validate_upload_batch,
    validate_upload_size,
)
from nanobot.utils.media_decode import media_destination


class AttachmentUploadError(ValueError):
    """An upload or reference failed validation without consuming the draft."""


@dataclass(frozen=True)
class StagedAttachment:
    owner: str
    path: Path
    mime: str
    size: int
    expires: float
    managed_reservation: CapacityReservation | None = None
    snapshot_reservation: CapacityReservation | None = None


@dataclass(frozen=True)
class CommittedAttachment:
    """离开暂存区、但尚未由接收方登记长期归属的文件。"""

    path: Path
    mime: str
    size: int
    managed_reservation: CapacityReservation | None
    snapshot_reservation: CapacityReservation | None


class AttachmentStore:
    """Single-event-loop staging store with disk, count, and lifetime bounds.

    A whole file's declared size is reserved before awaiting its first chunk,
    so concurrent slow uploads cannot bypass the global disk quota. ``prune``
    should also be called periodically by the owning gateway. Committed files
    leave this store and are managed by the existing session media lifecycle.
    """

    def __init__(
        self,
        media_dir: Path,
        *,
        limits: AttachmentIngressLimits | None = None,
        max_pending: int = DEFAULT_MAX_PENDING,
        max_pending_bytes: int = DEFAULT_MAX_PENDING_BYTES,
        ttl_seconds: float = 600,
        upload_timeout: float = UPLOAD_TIMEOUT_SECONDS,
        upload_idle_timeout: float = UPLOAD_IDLE_TIMEOUT_SECONDS,
        managed_store: ManagedAttachmentStore | None = None,
    ) -> None:
        if min(max_pending, max_pending_bytes, ttl_seconds, upload_timeout, upload_idle_timeout) <= 0:
            raise ValueError("Attachment store bounds must be positive")
        self.media_dir = media_dir
        self.limits = limits or AttachmentIngressLimits()
        self.max_pending = max_pending
        self.max_pending_bytes = max_pending_bytes
        self.ttl_seconds = ttl_seconds
        self.upload_timeout = upload_timeout
        self.upload_idle_timeout = upload_idle_timeout
        self.managed_store = managed_store
        self._entries: dict[str, StagedAttachment] = {}
        self._reserved_bytes = 0
        self._active = 0
        self._journal = media_dir / ".pending-attachments"

    async def upload(
        self,
        chunks: AsyncIterable[bytes],
        *,
        owner: str,
        mime: str,
        size: int,
        filename: str | None = None,
    ) -> str:
        """Stream a declared-length binary body and return an opaque reference.

        No body bytes are requested until metadata and capacity are validated.
        The adapter must disable automatic request decompression and reject
        ambiguous framing, and must not buffer the complete body beforehand.
        """
        self.prune()
        if not owner:
            raise AttachmentUploadError("Missing authenticated owner")
        if mime not in UPLOAD_MIME_ALLOWED:
            raise AttachmentUploadError("Unsupported attachment MIME")
        try:
            validate_upload_size(mime, size, limits=self.limits)
        except ValueError as exc:
            raise AttachmentUploadError(str(exc)) from exc
        if self._active + len(self._entries) >= self.max_pending:
            raise AttachmentUploadError(
                f"Too many pending attachments (maximum {self.max_pending})"
            )
        if self._reserved_bytes + size > self.max_pending_bytes:
            raise AttachmentUploadError(
                "Pending attachment storage is full "
                f"(maximum {self.max_pending_bytes:,} bytes)"
            )
        self._active += 1
        self._reserved_bytes += size
        path: Path | None = None
        stored = False
        created = False
        marker_created = False
        managed_reservation: CapacityReservation | None = None
        snapshot_reservation: CapacityReservation | None = None
        try:
            if self.managed_store is not None:
                try:
                    managed_reservation = self.managed_store.reserve(
                        size, purpose="attachment-upload",
                    )
                    if mime.startswith("image/"):
                        snapshot_size = (
                            len(f"data:{mime};base64,".encode("ascii"))
                            + 4 * ((size + 2) // 3)
                        )
                        snapshot_reservation = self.managed_store.reserve(
                            snapshot_size, purpose="image-snapshot",
                        )
                except AttachmentCapacityError as exc:
                    raise AttachmentUploadError(str(exc)) from exc
            self.media_dir.mkdir(parents=True, exist_ok=True)
            path = media_destination(
                self.media_dir, mime,
                filename=filename if mime in DOCUMENT_MIME_ALLOWED else None,
            )
            self._journal.mkdir(parents=True, exist_ok=True)
            (self._journal / path.name).touch(exist_ok=False)
            marker_created = True
            received = 0
            # Exclusive creation prevents accidental overwrite or symlink following.
            with path.open("xb") as output:
                created = True
                async with asyncio.timeout(self.upload_timeout):
                    iterator = aiter(chunks)
                    while True:
                        async with asyncio.timeout(self.upload_idle_timeout):
                            try:
                                chunk = await anext(iterator)
                            except StopAsyncIteration:
                                break
                        received += len(chunk)
                        if received > size:
                            raise AttachmentUploadError("Attachment exceeds declared size")
                        output.write(chunk)
            if received != size:
                raise AttachmentUploadError("Incomplete attachment body")
            reference = secrets.token_urlsafe(32)
            self._entries[reference] = StagedAttachment(
                owner,
                path,
                mime,
                size,
                time.monotonic() + self.ttl_seconds,
                managed_reservation,
                snapshot_reservation,
            )
            stored = True
            return reference
        finally:
            self._active -= 1
            if not stored:
                self._reserved_bytes -= size
                if managed_reservation is not None:
                    managed_reservation.release()
                if snapshot_reservation is not None:
                    snapshot_reservation.release()
                if path is not None:
                    if created:
                        path.unlink(missing_ok=True)
                    if marker_created:
                        (self._journal / path.name).unlink(missing_ok=True)

    def _inspect_owned(
        self,
        references: list[str],
        *,
        owner: str,
    ) -> list[StagedAttachment]:
        entries: list[StagedAttachment] = []
        for reference in references:
            entry = self._entries.get(reference)
            if entry is None or not owner or entry.owner != owner:
                raise AttachmentUploadError("Invalid attachment reference")
            entries.append(entry)
        return entries

    def _release_committed(self, reference: str) -> None:
        entry = self._entries[reference]
        (self._journal / entry.path.name).unlink(missing_ok=True)
        self._entries.pop(reference)
        self._reserved_bytes -= entry.size

    def inspect(self, references: list[str], *, owner: str) -> list[StagedAttachment]:
        """Validate a complete message batch without consuming it."""
        self.prune()
        if len(references) > self.limits.max_attachments:
            raise AttachmentUploadError(
                f"Too many attachments (maximum {self.limits.max_attachments})"
            )
        if len(set(references)) != len(references):
            raise AttachmentUploadError("Duplicate attachment reference")
        entries = self._inspect_owned(references, owner=owner)
        try:
            validate_upload_batch([(e.mime, e.size) for e in entries], limits=self.limits)
        except ValueError as exc:
            raise AttachmentUploadError(str(exc)) from exc
        return entries

    def inspect_single(self, reference: str, *, owner: str) -> StagedAttachment:
        """Validate one non-message file without applying batch total limits."""
        self.prune()
        entry = self._inspect_owned([reference], owner=owner)[0]
        try:
            if entry.mime not in UPLOAD_MIME_ALLOWED:
                raise ValueError("Unsupported attachment MIME")
            validate_upload_size(entry.mime, entry.size, limits=self.limits)
        except ValueError as exc:
            raise AttachmentUploadError(str(exc)) from exc
        return entry

    def resolve(self, references: list[str], *, owner: str) -> list[str]:
        return [str(entry.path) for entry in self.inspect(references, owner=owner)]

    @property
    def pending_bytes(self) -> int:
        return self._reserved_bytes

    def commit(self, references: list[str], *, owner: str) -> list[str]:
        """Transfer validated files to session ownership after message acceptance.

        The caller must not await between resolve, synchronous acceptance, and
        commit. A rejected message must leave references staged for retry.
        """
        committed = self.commit_managed(references, owner=owner)
        for entry in committed:
            if entry.managed_reservation is not None:
                entry.managed_reservation.release()
            if entry.snapshot_reservation is not None:
                entry.snapshot_reservation.release()
        return [str(entry.path) for entry in committed]

    def commit_managed(self, references: list[str], *, owner: str) -> list[CommittedAttachment]:
        """转移暂存文件并返回接收方必须登记或释放的长期额度预留。"""
        self.inspect(references, owner=owner)
        committed: list[CommittedAttachment] = []
        for reference in references:
            entry = self._entries[reference]
            committed.append(CommittedAttachment(
                path=entry.path,
                mime=entry.mime,
                size=entry.size,
                managed_reservation=entry.managed_reservation,
                snapshot_reservation=entry.snapshot_reservation,
            ))
            self._release_committed(reference)
        return committed

    def commit_single(self, reference: str, *, owner: str) -> str:
        """Transfer one non-message file without applying message batch limits."""
        entry = self.inspect_single(reference, owner=owner)
        self._release_committed(reference)
        return str(entry.path)

    def discard_owner(self, owner: str) -> None:
        self.discard([ref for ref, entry in self._entries.items() if entry.owner == owner], owner=owner)

    def discard(self, references: list[str], *, owner: str) -> None:
        for reference in references:
            entry = self._entries.get(reference)
            if entry is not None and entry.owner == owner:
                entry.path.unlink(missing_ok=True)
                (self._journal / entry.path.name).unlink(missing_ok=True)
                self._reserved_bytes -= entry.size
                _release_staged_reservations(entry)
                del self._entries[reference]

    def prune(self) -> None:
        """Remove abandoned uploads; never delete committed session media."""
        now = time.monotonic()
        # Crash remnants retain a marker; committed historical files never do.
        cutoff = time.time() - self.ttl_seconds - self.upload_timeout
        for marker in self._journal.glob("*"):
            if marker.is_file() and marker.stat().st_mtime < cutoff:
                (self.media_dir / marker.name).unlink(missing_ok=True)
                marker.unlink(missing_ok=True)
        for reference, entry in list(self._entries.items()):
            if entry.expires <= now:
                entry.path.unlink(missing_ok=True)
                (self._journal / entry.path.name).unlink(missing_ok=True)
                self._reserved_bytes -= entry.size
                _release_staged_reservations(entry)
                del self._entries[reference]

    def clear(self) -> None:
        """Clean staging after the gateway has stopped accepting uploads."""
        if self._active:
            raise RuntimeError("Stop active uploads before clearing attachment storage")
        for reference, entry in list(self._entries.items()):
            entry.path.unlink(missing_ok=True)
            (self._journal / entry.path.name).unlink(missing_ok=True)
            self._reserved_bytes -= entry.size
            _release_staged_reservations(entry)
            del self._entries[reference]


def _release_staged_reservations(entry: StagedAttachment) -> None:
    if entry.managed_reservation is not None:
        entry.managed_reservation.release()
    if entry.snapshot_reservation is not None:
        entry.snapshot_reservation.release()
