"""编排实例级附件盘点、归属与生命周期。"""

from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import os
import re
import threading
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal, cast

from nanobot.attachments.references import (
    AttachmentReferenceScan,
    scan_session_references,
)
from nanobot.attachments.store import (
    AttachmentDeletePreview,
    AttachmentDeleteResult,
    AttachmentFileSpec,
    AttachmentRecord,
    AttachmentReference,
    AttachmentStore,
    AttachmentUsage,
    CapacityReservation,
)
from nanobot.utils.atomic_file import atomic_write_text, io_path
from nanobot.utils.helpers import detect_image_mime

_UPLOAD_NAME = re.compile(r"^[0-9a-f]{12}(?:_[^/]*)?(?:\.[^/]+)$")
_OUTBOUND_NAME = re.compile(r"^([0-9a-f]{20})-.+")
_SNAPSHOT_NAME = re.compile(r"^[0-9a-f]{64}$")
_GENERATED_NAME = re.compile(r"^img_[0-9a-f]{12}(?:\.[^.]+)$")
_MIME_EXTENSIONS = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
}


@dataclass(frozen=True)
class AttachmentPolicy:
    max_bytes: int | None
    auto_cleanup: bool
    orphan_days: int

    def payload(self) -> dict[str, object]:
        return {
            "max_bytes": self.max_bytes,
            "auto_cleanup": self.auto_cleanup,
            "orphan_days": self.orphan_days,
        }


class AttachmentService:
    """组合引用盘点、文件归属和管理操作，不依赖 WebUI 或渠道运行时。"""

    def __init__(
        self,
        *,
        data_dir: Path,
        sessions_root: Path,
        max_bytes: int | None = None,
        auto_cleanup: bool = False,
        orphan_days: int = 30,
    ) -> None:
        self.data_dir = data_dir.expanduser().resolve(strict=False)
        self.sessions_root = sessions_root.expanduser().resolve(strict=False)
        self.store = AttachmentStore(self.data_dir, max_bytes=max_bytes)
        self.auto_cleanup = auto_cleanup
        self.orphan_days = orphan_days
        self._last_inventory: AttachmentReferenceScan | None = None
        self._temporary_paths: set[str] = set()
        self._lifecycle_lock = threading.RLock()

    def update_policy(
        self,
        *,
        max_bytes: int | None,
        auto_cleanup: bool,
        orphan_days: int,
    ) -> None:
        if isinstance(orphan_days, bool) or orphan_days <= 0:
            raise ValueError("orphan_days must be a positive integer")
        self.store.set_max_bytes(max_bytes)
        self.auto_cleanup = bool(auto_cleanup)
        self.orphan_days = orphan_days

    @property
    def policy(self) -> AttachmentPolicy:
        return AttachmentPolicy(
            max_bytes=self.store.max_bytes,
            auto_cleanup=self.auto_cleanup,
            orphan_days=self.orphan_days,
        )

    def reserve(self, size: int, *, purpose: str = "attachment") -> CapacityReservation:
        return self.store.reserve(size, purpose=purpose)

    def reserve_image_snapshot(self, mime: str, size: int) -> CapacityReservation:
        """为即将生成的图片 data URL 副本预留上界。"""
        if not mime.startswith("image/"):
            raise ValueError("snapshot reservation requires an image MIME")
        snapshot_size = len(f"data:{mime};base64,".encode("ascii")) + 4 * ((size + 2) // 3)
        return self.store.reserve(snapshot_size, purpose="image-snapshot")

    def protect_borrowed_sources(self, paths: list[Path]) -> set[str]:
        """持久保护明确的借用原件，忽略托管根外文件。"""
        protected: set[str] = set()
        for path in paths:
            relative = self.store.protect_source(path, reason="borrowed")
            if relative is not None:
                protected.add(relative)
        return protected

    def retain_temporary_paths(self, paths: Iterable[Path]) -> None:
        """在现有临时聊天生命周期内排除其文件。"""
        retained: set[str] = set()
        for path in paths:
            try:
                retained.add(self.store.relative_path(path, require_file=False))
            except (OSError, ValueError):
                continue
        if retained:
            with self._lifecycle_lock:
                self._temporary_paths.update(retained)

    def release_temporary_paths(self, paths: Iterable[Path]) -> None:
        """临时聊天清理文件前解除排除。"""
        released: set[str] = set()
        for path in paths:
            try:
                released.add(self.store.relative_path(path, require_file=False))
            except (OSError, ValueError):
                continue
        if released:
            with self._lifecycle_lock:
                self._temporary_paths.difference_update(released)

    def register_upload(
        self,
        path: Path,
        *,
        name: str | None,
        mime: str,
        session_key: str,
        reservation: CapacityReservation | None = None,
    ) -> AttachmentRecord:
        kind: Literal["image", "video", "file"]
        if mime.startswith("image/"):
            kind = "image"
        elif mime.startswith("video/"):
            kind = "video"
        else:
            kind = "file"
        existing = self.store.attachment_for_path(path)
        if existing is not None:
            if reservation is not None:
                reservation.release()
            return existing
        return self.store.register(
            name=name or path.name,
            kind=kind,
            source="upload",
            files=[AttachmentFileSpec(path=path, mime=mime)],
            owner_session_key=session_key,
            references=[AttachmentReference(session_key, session_key)],
            reservation=reservation,
        )

    def ensure_image_snapshot(
        self,
        source_path: Path | None,
        data_url: str | None,
        *,
        session_key: str,
        source_attachment_id: str | None = None,
        reservation: CapacityReservation | None = None,
    ) -> AttachmentRecord:
        """写入或复用共享图片副本，并登记来源关联。"""
        if data_url is None:
            if source_path is None:
                raise ValueError("image snapshot requires a source path or data URL")
            raw = source_path.read_bytes()
            mime = detect_image_mime(raw)
            if mime is None:
                raise ValueError("source file is not a supported image")
            data_url = f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"
        image_id = hashlib.sha256(data_url.encode("ascii")).hexdigest()
        target = self.data_dir / "session-images" / image_id
        existing = self._find_snapshot(image_id, session_key, source_attachment_id)
        if existing is not None:
            if reservation is not None:
                reservation.release()
            return existing
        source = (
            self.store.get(source_attachment_id)
            if source_attachment_id is not None
            else self.store.attachment_for_path(source_path)
            if source_path is not None
            else None
        )
        for record in self.store.records_for_path(target):
            if record.status != "deleted" and record.owner_session_key == session_key and (
                source_attachment_id is None or record.id == source_attachment_id
            ):
                if reservation is not None:
                    reservation.release()
                return record
        directory = self.data_dir / "session-images"
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        created = False
        if reservation is None and not target.is_file():
            reservation = self.reserve(len(data_url.encode("ascii")), purpose="image-snapshot")
        try:
            if not target.is_file():
                atomic_write_text(io_path(target), data_url)
                created = True
            source_id = source.id if source is not None else None
            if source is not None and source.source != "image_snapshot":
                if reservation is None and not self._file_is_registered(target):
                    reservation = self.reserve(
                        len(data_url.encode("ascii")),
                        purpose="image-snapshot",
                    )
                return self.store.add_file(
                    source.id,
                    AttachmentFileSpec(path=target, role="image_snapshot", mime="text/plain"),
                    reservation=reservation,
                )
            attachment_id = (
                f"snap_{source_id}"
                if source_id
                else f"snap_{uuid.uuid4().hex}"
            )
            record = self.store.register(
                name=target.name,
                kind="image",
                source="image_snapshot",
                files=[AttachmentFileSpec(path=target, mime="text/plain")],
                owner_session_key=session_key,
                metadata={
                    "image_id": image_id,
                    "source_attachment_id": source_id,
                    "session_key": session_key,
                    **(
                        {
                            "source_path": os.path.normcase(
                                str(source_path.expanduser().resolve(strict=False))
                            )
                        }
                        if source_path is not None and source is None
                        else {}
                    ),
                },
                references=[AttachmentReference(session_key, session_key)],
                attachment_id=attachment_id,
                reservation=reservation,
            )
            return record
        except BaseException:
            if created:
                target.unlink(missing_ok=True)
            if reservation is not None:
                reservation.release()
            raise

    def _file_is_registered(self, path: Path) -> bool:
        try:
            return bool(self.store.records_for_path(path))
        except (OSError, ValueError):
            return False

    def register_outbound_copy(
        self,
        staged_path: Path,
        *,
        source_path: Path,
        source_digest: str,
        session_key: str,
        reservation: CapacityReservation,
    ) -> AttachmentRecord:
        existing = self.find_outbound_copy(source_digest, session_key)
        if existing is not None:
            reservation.release()
            return existing
        attachment_id = f"out_{source_digest[:16]}_{_session_digest(session_key)[:10]}"
        return self.store.register(
            name=source_path.name,
            kind=_kind_for_name(source_path.name),
            source="outbound_copy",
            files=[AttachmentFileSpec(staged_path)],
            owner_session_key=session_key,
            metadata={
                "source_digest": source_digest,
                "source_path": os.path.normcase(
                    str(source_path.expanduser().resolve(strict=False))
                ),
                "session_key": session_key,
            },
            references=[AttachmentReference(session_key, session_key)],
            attachment_id=attachment_id,
            reservation=reservation,
        )

    def find_outbound_copy(
        self,
        source_digest: str,
        session_key: str,
        *,
        include_deleted: bool = False,
    ) -> AttachmentRecord | None:
        for record in self.store.find_by_metadata(
            "source_digest", source_digest, include_deleted=include_deleted,
        ):
            if record.metadata.get("session_key") == session_key:
                return record
        return None

    def find_outbound_for_source_path(
        self,
        source_path: Path,
        session_key: str,
        *,
        include_deleted: bool = False,
    ) -> AttachmentRecord | None:
        canonical = os.path.normcase(str(source_path.expanduser().resolve(strict=False)))
        for record in self.store.find_by_metadata(
            "source_path", canonical, include_deleted=include_deleted,
        ):
            if record.metadata.get("session_key") == session_key:
                return record
        return None

    def find_image_snapshot_for_source_path(
        self,
        source_path: Path,
        session_key: str,
        *,
        include_deleted: bool = False,
    ) -> AttachmentRecord | None:
        canonical = os.path.normcase(str(source_path.expanduser().resolve(strict=False)))
        for record in self.store.find_by_metadata(
            "source_path", canonical, include_deleted=include_deleted,
        ):
            if (
                record.source == "image_snapshot"
                and record.metadata.get("session_key") == session_key
            ):
                return record
        return None

    def register_generated(
        self,
        image_path: Path,
        metadata_path: Path,
        *,
        session_key: str,
        metadata: dict[str, object],
        reservation: CapacityReservation,
    ) -> AttachmentRecord:
        artifact_id = str(metadata.get("id") or image_path.stem)
        return self.store.register(
            name=image_path.name,
            kind="image",
            source="generated",
            files=[
                AttachmentFileSpec(image_path, role="primary", mime=str(metadata.get("mime") or "")),
                AttachmentFileSpec(metadata_path, role="metadata", mime="application/json"),
            ],
            owner_session_key=session_key,
            metadata={**metadata, "session_key": session_key},
            references=[AttachmentReference(session_key, session_key)],
            attachment_id=f"gen_{artifact_id}",
            reservation=reservation,
        )

    def reconcile(self) -> AttachmentReferenceScan:
        """登记可确认来源的旧文件并重算所有会话引用。"""
        scan = scan_session_references(self.sessions_root, data_dir=self.data_dir)
        self._register_inventory(scan)
        if not scan.complete:
            self._last_inventory = scan
            return scan
        reference_map = self._reference_map(scan)
        self.store.replace_references(reference_map)
        self.store.reconcile_statuses()
        self._last_inventory = scan
        return scan

    def usage(self, *, pending_bytes: int = 0) -> AttachmentUsage:
        return self.store.usage(
            pending_bytes=pending_bytes,
            excluded_paths=self._temporary_path_snapshot(),
        )

    def management_payload(
        self,
        *,
        pending_bytes: int = 0,
        reconcile: bool = True,
    ) -> dict[str, object]:
        if reconcile:
            self.reconcile()
        temporary = self._temporary_path_snapshot()
        records = [
            record for record in self.store.list_active()
            if not self._record_is_temporary(record, temporary)
        ]
        return {
            "items": [record.management_payload() for record in records],
            "usage": self.store.usage(
                pending_bytes=pending_bytes,
                excluded_paths=temporary,
            ).payload(),
            "settings": self.policy.payload(),
        }

    def preview_delete(self, attachment_ids: list[str]) -> AttachmentDeletePreview:
        self.reconcile()
        expanded = self._include_dependents(attachment_ids)
        return self.store.preview_delete(
            expanded,
            excluded_paths=self._temporary_path_snapshot(),
        )

    def delete(self, attachment_ids: list[str]) -> AttachmentDeleteResult:
        self.reconcile()
        expanded = self._include_dependents(attachment_ids)
        return self.store.delete(
            expanded,
            excluded_paths=self._temporary_path_snapshot(),
        )

    def reclaim_deleted_sessions(self, session_keys: set[str]) -> AttachmentDeleteResult:
        """删除会话后立即回收其独占且已无引用的托管附件。"""
        if not session_keys:
            return AttachmentDeleteResult((), (), 0, ())
        involved = {
            record.id
            for record in self.store.attachments_for_sessions(session_keys)
        }
        scan = self.reconcile()
        if not scan.complete:
            return AttachmentDeleteResult((), (), 0, ())
        candidates = [
            record.id
            for record in self.store.list_active()
            if (
                record.id in involved
                or record.owner_session_key in session_keys
            )
            and not record.references
        ]
        if not candidates:
            return AttachmentDeleteResult((), (), 0, ())
        return self.store.delete(candidates)

    def cleanup_orphans(self, *, now: datetime | None = None) -> AttachmentDeleteResult:
        if not self.auto_cleanup:
            return AttachmentDeleteResult((), (), 0, ())
        scan = self.reconcile()
        if not scan.complete:
            return AttachmentDeleteResult((), (), 0, ())
        return self.store.cleanup_orphans(
            orphan_days=self.orphan_days,
            now=now,
            excluded_paths=self._temporary_path_snapshot(),
        )

    def cleanup_if_due(self) -> AttachmentDeleteResult:
        return self.cleanup_orphans() if self.auto_cleanup else AttachmentDeleteResult((), (), 0, ())

    def _find_snapshot(
        self,
        image_id: str,
        session_key: str,
        source_attachment_id: str | None,
    ) -> AttachmentRecord | None:
        for record in self.store.find_by_metadata("image_id", image_id):
            if record.metadata.get("session_key") != session_key:
                continue
            if source_attachment_id is not None:
                if record.metadata.get("source_attachment_id") == source_attachment_id:
                    return record
            else:
                return record
        return None

    def _register_inventory(self, scan: AttachmentReferenceScan) -> None:
        self.protect_borrowed_sources([Path(path) for path in scan.borrowed_paths])
        protected = self.store.protected_paths()
        temporary = self._temporary_path_snapshot()
        referenced_paths = {_canonical_path(path) for path in scan.paths}
        for path in self._candidate_files():
            canonical = _canonical_path(path)
            relative = self.store.relative_path(path, require_file=True)
            if relative in protected or relative in temporary:
                continue
            existing = self.store.attachment_for_path(path)
            if existing is not None and existing.status != "deleted":
                continue
            source, name, kind, attachment_id = _classify_path(relative)
            if source is None:
                continue
            references = self._reference_values(
                scan, {canonical}, set(),
            )
            self.store.register(
                name=name,
                kind=kind,
                source=source,
                files=[AttachmentFileSpec(path)],
                metadata={"inventory": True},
                references=references,
                attachment_id=attachment_id,
                initially_referenced=canonical in referenced_paths,
            )
        self._register_generated_metadata(scan)

    def _register_generated_metadata(self, scan: AttachmentReferenceScan) -> None:
        media_root = self.data_dir / "media"
        if not media_root.is_dir() or media_root.is_symlink():
            return
        for metadata_path in media_root.rglob("*.json"):
            if metadata_path.is_symlink() or not metadata_path.is_file():
                continue
            try:
                if metadata_path.stat().st_size > 1024 * 1024:
                    continue
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                continue
            if not isinstance(metadata, dict):
                continue
            data = cast(dict[str, object], metadata)
            artifact_id = data.get("id")
            raw_path = data.get("path")
            mime = data.get("mime")
            if not isinstance(artifact_id, str) or not _GENERATED_NAME.fullmatch(f"{artifact_id}.png"):
                if not isinstance(artifact_id, str) or _GENERATED_NAME.fullmatch(metadata_path.stem + ".png") is None:
                    continue
            if not isinstance(raw_path, str) or not isinstance(mime, str):
                continue
            image_path = Path(raw_path)
            try:
                image_path.resolve(strict=True).relative_to(media_root.resolve())
            except (OSError, ValueError):
                continue
            if (
                not image_path.is_file()
                or image_path.parent.resolve() != metadata_path.parent.resolve()
                or image_path.stem != metadata_path.stem
            ):
                continue
            existing = self.store.attachment_for_path(image_path)
            if existing is not None and existing.status != "deleted":
                continue
            canonical = _canonical_path(image_path)
            references = self._reference_values(scan, {canonical}, set())
            self.store.register(
                name=image_path.name,
                kind="image",
                source="generated",
                files=[
                    AttachmentFileSpec(image_path, role="primary", mime=mime),
                    AttachmentFileSpec(metadata_path, role="metadata", mime="application/json"),
                ],
                metadata={**data, "inventory": True},
                references=references,
                attachment_id=f"gen_{artifact_id}",
                initially_referenced=canonical in {_canonical_path(path) for path in scan.paths},
            )

    def _candidate_files(self) -> list[Path]:
        candidates: list[Path] = []
        websocket = self.data_dir / "media" / "websocket"
        if websocket.is_dir() and not websocket.is_symlink():
            journal = websocket / ".pending-attachments"
            pending_names: set[str] = {
                marker.name
                for marker in journal.iterdir()
                if marker.is_file()
            } if journal.is_dir() and not journal.is_symlink() else set()
            for path in websocket.iterdir():
                if path.is_symlink() or not path.is_file():
                    continue
                if path.name in pending_names:
                    continue
                if _UPLOAD_NAME.fullmatch(path.name) or _OUTBOUND_NAME.fullmatch(path.name):
                    candidates.append(path)
        snapshots = self.data_dir / "session-images"
        if snapshots.is_dir() and not snapshots.is_symlink():
            for path in snapshots.iterdir():
                if not path.is_symlink() and path.is_file() and _SNAPSHOT_NAME.fullmatch(path.name):
                    candidates.append(path)
        return candidates

    def _temporary_path_snapshot(self) -> frozenset[str]:
        with self._lifecycle_lock:
            return frozenset(self._temporary_paths)

    @staticmethod
    def _record_is_temporary(
        record: AttachmentRecord,
        temporary: frozenset[str],
    ) -> bool:
        return any(file.relative_path in temporary for file in record.files)

    def _reference_map(
        self, scan: AttachmentReferenceScan,
    ) -> dict[str, set[AttachmentReference]]:
        mapping: dict[str, set[AttachmentReference]] = {}
        for record in self.store.list_active():
            references: set[AttachmentReference] = set()
            source_path = record.metadata.get("source_path")
            source_canonical = (
                _canonical_path(source_path)
                if isinstance(source_path, str)
                else None
            )
            for file in record.files:
                absolute = self.store.resolve_relative(file.relative_path)
                canonical = _canonical_path(absolute)
                for session in scan.sessions.values():
                    if canonical in {_canonical_path(path) for path in session.paths}:
                        references.add(AttachmentReference(session.key, session.title))
            for session in scan.sessions.values():
                if (
                    record.id in session.managed_ids
                    or (
                        source_canonical is not None
                        and source_canonical in {_canonical_path(path) for path in session.paths}
                    )
                ):
                    references.add(AttachmentReference(session.key, session.title))
            mapping[record.id] = references

        for record in self.store.list_active():
            source_paths = record.metadata.get("source_images")
            if record.source != "generated" or not isinstance(source_paths, list):
                continue
            session_refs = mapping.get(record.id, set())
            for raw in cast(list[object], source_paths):
                if not isinstance(raw, str):
                    continue
                source = self.store.attachment_for_path(Path(raw))
                if source is not None:
                    mapping.setdefault(source.id, set()).update(session_refs)
        return mapping

    def _reference_values(
        self,
        scan: AttachmentReferenceScan,
        paths: set[str],
        managed_ids: set[str],
    ) -> list[AttachmentReference]:
        references: list[AttachmentReference] = []
        for session in scan.sessions.values():
            session_paths = {_canonical_path(path) for path in session.paths}
            if paths & session_paths or managed_ids & set(session.managed_ids):
                references.append(AttachmentReference(session.key, session.title))
        return references

    def _include_dependents(self, attachment_ids: list[str]) -> list[str]:
        expanded = list(dict.fromkeys(attachment_ids))
        changed = True
        while changed:
            changed = False
            selected = set(expanded)
            for record in self.store.list_active():
                source_id = record.metadata.get("source_attachment_id")
                if isinstance(source_id, str) and source_id in selected and record.id not in selected:
                    expanded.append(record.id)
                    changed = True
        return expanded


class AttachmentServiceRegistry:
    """同一进程内按实例数据目录共享服务与额度预留。"""

    def __init__(self) -> None:
        self._services: dict[Path, AttachmentService] = {}

    def get(
        self,
        *,
        data_dir: Path,
        sessions_root: Path,
        max_bytes: int | None = None,
        auto_cleanup: bool = False,
        orphan_days: int = 30,
    ) -> AttachmentService:
        key = data_dir.expanduser().resolve(strict=False)
        service = self._services.get(key)
        if service is None:
            service = AttachmentService(
                data_dir=key,
                sessions_root=sessions_root,
                max_bytes=max_bytes,
                auto_cleanup=auto_cleanup,
                orphan_days=orphan_days,
            )
            self._services[key] = service
        else:
            service.sessions_root = sessions_root.expanduser().resolve(strict=False)
            service.update_policy(
                max_bytes=max_bytes,
                auto_cleanup=auto_cleanup,
                orphan_days=orphan_days,
            )
        return service

    def existing(self, data_dir: Path) -> AttachmentService | None:
        return self._services.get(data_dir.expanduser().resolve(strict=False))

    def clear(self) -> None:
        self._services.clear()


def _classify_path(
    relative: str,
) -> tuple[
    Literal["upload", "image_snapshot", "outbound_copy"] | None,
    str,
    Literal["image", "video", "file"],
    str,
]:
    path = Path(relative)
    digest = hashlib.sha256(relative.encode("utf-8")).hexdigest()[:20]
    if path.parent.as_posix() == "session-images" and _SNAPSHOT_NAME.fullmatch(path.name):
        return "image_snapshot", path.name, "image", f"snap_file_{digest}"
    if path.parent.as_posix() == "media/websocket":
        outbound = _OUTBOUND_NAME.fullmatch(path.name)
        if outbound is not None:
            return "outbound_copy", path.name, _kind_for_name(path.name), f"out_{outbound.group(1)}_{digest}"
        if _UPLOAD_NAME.fullmatch(path.name):
            return "upload", path.name, _kind_for_name(path.name), f"upload_{digest}"
    return None, path.name, "file", f"file_{digest}"


def _kind_for_name(name: str) -> Literal["image", "video", "file"]:
    mime = mimetypes.guess_type(name)[0] or ""
    if mime.startswith("image/"):
        return "image"
    if mime.startswith("video/"):
        return "video"
    return "file"


def _canonical_path(value: str | Path) -> str:
    try:
        return os.path.normcase(str(Path(value).expanduser().resolve(strict=False)))
    except OSError:
        return os.path.normcase(str(value))


def _session_digest(session_key: str) -> str:
    return hashlib.sha256(session_key.encode("utf-8")).hexdigest()
