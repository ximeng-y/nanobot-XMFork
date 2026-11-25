"""使用 SQLite 管理附件归属、容量、删除与清理。"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Literal

AttachmentSource = Literal["upload", "image_snapshot", "generated", "outbound_copy"]
AttachmentKind = Literal["image", "video", "file"]
AttachmentStatus = Literal["available", "missing", "deleted"]

_SOURCES = frozenset({"upload", "image_snapshot", "generated", "outbound_copy"})
_KINDS = frozenset({"image", "video", "file"})
_STATUSES = frozenset({"available", "missing", "deleted"})
_MANAGED_ROOTS = frozenset({"media", "session-images"})


class AttachmentCapacityError(ValueError):
    """新增托管附件会超过长期容量。"""


class AttachmentPathError(ValueError):
    """托管路径越界、为符号链接或不符合登记格式。"""


@dataclass(frozen=True)
class AttachmentReference:
    """一个引用附件的会话。"""

    key: str
    title: str


@dataclass(frozen=True)
class AttachmentFile:
    """附件关联的实例内实际文件。"""

    relative_path: str
    bytes: int
    role: str = "primary"
    mime: str | None = None
    sha256: str | None = None


@dataclass(frozen=True)
class AttachmentFileSpec:
    """登记前给出的实际文件与用途。"""

    path: Path
    role: str = "primary"
    mime: str | None = None
    sha256: str | None = None


@dataclass(frozen=True)
class AttachmentRecord:
    """一个逻辑托管附件及其文件、来源和会话引用。"""

    id: str
    name: str
    kind: AttachmentKind
    source: AttachmentSource
    bytes: int
    created_at: str
    unreferenced_since: str | None
    status: AttachmentStatus
    owner_session_key: str | None
    files: tuple[AttachmentFile, ...] = ()
    references: tuple[AttachmentReference, ...] = ()
    metadata: dict[str, object] = field(default_factory=dict)

    def management_payload(self) -> dict[str, object]:
        """返回固定管理合同中的单个附件字段。"""
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "source": self.source,
            "bytes": self.bytes,
            "created_at": self.created_at,
            "unreferenced_since": self.unreferenced_since,
            "references": [
                {"key": reference.key, "title": reference.title}
                for reference in self.references
            ],
            "status": self.status,
        }


@dataclass(frozen=True)
class AttachmentUsage:
    """管理接口使用的三类占用。"""

    managed_bytes: int
    pending_bytes: int
    protected_bytes: int

    def payload(self) -> dict[str, int]:
        return {
            "managed_bytes": self.managed_bytes,
            "pending_bytes": self.pending_bytes,
            "protected_bytes": self.protected_bytes,
        }


@dataclass(frozen=True)
class AttachmentDeletePreview:
    """删除影响预览，不执行磁盘修改。"""

    items: tuple[dict[str, object], ...]
    affected_sessions: tuple[AttachmentReference, ...]
    estimated_freed_bytes: int
    blocked: tuple[dict[str, str], ...]

    def payload(self) -> dict[str, object]:
        return {
            "items": list(self.items),
            "affected_sessions": [
                {"key": reference.key, "title": reference.title}
                for reference in self.affected_sessions
            ],
            "estimated_freed_bytes": self.estimated_freed_bytes,
            "blocked": list(self.blocked),
        }


@dataclass(frozen=True)
class AttachmentDeleteResult:
    """一次删除请求的实际结果。"""

    deleted: tuple[str, ...]
    failed: tuple[dict[str, str], ...]
    freed_bytes: int
    affected_sessions: tuple[AttachmentReference, ...]

    def payload(self) -> dict[str, object]:
        return {
            "deleted": list(self.deleted),
            "failed": list(self.failed),
            "freed_bytes": self.freed_bytes,
            "affected_sessions": [
                {"key": reference.key, "title": reference.title}
                for reference in self.affected_sessions
            ],
        }


@dataclass
class CapacityReservation:
    """在文件写入前预留的长期字节额度。"""

    token: str
    bytes: int
    _store: "AttachmentStore" = field(repr=False)
    _released: bool = field(default=False, init=False, repr=False)

    def release(self) -> None:
        if not self._released:
            self._store.release(self)
            self._released = True


class AttachmentStore:
    """单一实例附件库，按稳定 ID 管理文件并串行化额度变更。"""

    def __init__(self, data_dir: Path, *, max_bytes: int | None = None) -> None:
        if max_bytes is not None and (isinstance(max_bytes, bool) or max_bytes <= 0):
            raise ValueError("max_bytes must be a positive integer or None")
        self.data_dir = data_dir.expanduser().resolve(strict=False)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "attachments.sqlite3"
        if self.db_path.is_symlink():
            raise RuntimeError("attachment database must not be a symlink")
        self._lock = threading.RLock()
        self._max_bytes = max_bytes
        self._reservations: dict[str, int] = {}
        self._initialize()

    @property
    def max_bytes(self) -> int | None:
        return self._max_bytes

    def set_max_bytes(self, value: int | None) -> None:
        if value is not None and (isinstance(value, bool) or value <= 0):
            raise ValueError("max_bytes must be a positive integer or None")
        with self._lock:
            self._max_bytes = value

    def reserve(self, size: int, *, purpose: str = "attachment") -> CapacityReservation:
        """为尚未落盘的新增内容预留额度；失败时在写入前抛出。"""
        if isinstance(size, bool) or size < 0:
            raise ValueError("reservation size must be a non-negative integer")
        with self._lock:
            managed = self._managed_bytes_locked()
            reserved = sum(self._reservations.values())
            if self._max_bytes is not None and managed + reserved + size > self._max_bytes:
                raise AttachmentCapacityError(
                    "Attachment storage limit reached; delete managed attachments before adding more"
                )
            token = f"{purpose}:{uuid.uuid4().hex}"
            self._reservations[token] = size
            return CapacityReservation(token, size, self)

    def release(self, reservation: CapacityReservation) -> None:
        with self._lock:
            self._reservations.pop(reservation.token, None)

    @property
    def reserved_bytes(self) -> int:
        with self._lock:
            return sum(self._reservations.values())

    def register(
        self,
        *,
        name: str,
        kind: AttachmentKind,
        source: AttachmentSource,
        files: Iterable[AttachmentFileSpec],
        owner_session_key: str | None = None,
        metadata: Mapping[str, object] | None = None,
        references: Iterable[AttachmentReference] = (),
        attachment_id: str | None = None,
        reservation: CapacityReservation | None = None,
        initially_referenced: bool = True,
    ) -> AttachmentRecord:
        """登记一个逻辑附件；同一物理路径可被多个逻辑附件共享。"""
        if kind not in _KINDS or source not in _SOURCES:
            raise ValueError("unsupported attachment kind or source")
        materialized = self._materialize_files(files)
        reference_values = tuple(sorted(set(references), key=lambda item: item.key))
        metadata_value = dict(metadata or {})
        attachment_id = attachment_id or f"att_{uuid.uuid4().hex}"
        now = _now_iso()

        with self._lock:
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                existing_rows = {
                    row["path"]: row
                    for row in connection.execute(
                        f"SELECT path, bytes FROM attachment_files WHERE path IN ({_placeholders(materialized)})",
                        tuple(file.relative_path for file in materialized),
                    )
                } if materialized else {}
                new_bytes = sum(
                    file.bytes for file in materialized if file.relative_path not in existing_rows
                )
                reserved_by_others = sum(self._reservations.values())
                if reservation is not None:
                    reserved_by_others -= reservation.bytes
                managed = self._managed_bytes_on(connection)
                if (
                    self._max_bytes is not None
                    and managed + reserved_by_others + new_bytes > self._max_bytes
                ):
                    raise AttachmentCapacityError(
                        "Attachment storage limit reached; delete managed attachments before adding more"
                    )
                connection.execute(
                    """INSERT INTO managed_attachments
                       (id, name, kind, source, created_at, status, unreferenced_since,
                        owner_session_key, metadata)
                       VALUES (?, ?, ?, ?, ?, 'available', ?, ?, ?)""",
                    (
                        attachment_id,
                        name,
                        kind,
                        source,
                        now,
                        None if initially_referenced else now,
                        owner_session_key,
                        _encode_json(metadata_value),
                    ),
                )
                for file in materialized:
                    existing = existing_rows.get(file.relative_path)
                    if existing is None:
                        connection.execute(
                            """INSERT INTO attachment_files
                               (path, bytes, role, mime, sha256)
                               VALUES (?, ?, ?, ?, ?)""",
                            (
                                file.relative_path,
                                file.bytes,
                                file.role,
                                file.mime,
                                file.sha256,
                            ),
                        )
                    connection.execute(
                        "INSERT INTO attachment_file_links (attachment_id, file_path) VALUES (?, ?)",
                        (attachment_id, file.relative_path),
                    )
                for reference in reference_values:
                    connection.execute(
                        """INSERT OR IGNORE INTO attachment_references
                           (attachment_id, session_key, title) VALUES (?, ?, ?)""",
                        (attachment_id, reference.key, reference.title),
                    )
                if reservation is not None:
                    self._reservations.pop(reservation.token, None)
                    reservation._released = True
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
            finally:
                connection.close()
            return self.get(attachment_id)

    def register_existing_file(
        self,
        path: Path,
        *,
        name: str | None = None,
        kind: AttachmentKind = "file",
        source: AttachmentSource = "upload",
        owner_session_key: str | None = None,
        references: Iterable[AttachmentReference] = (),
        initially_referenced: bool = True,
    ) -> AttachmentRecord:
        """登记一个已经由应用写入的实例内文件。"""
        return self.register(
            name=name or path.name,
            kind=kind,
            source=source,
            files=[AttachmentFileSpec(path)],
            owner_session_key=owner_session_key,
            references=references,
            initially_referenced=initially_referenced,
        )

    def add_file(
        self,
        attachment_id: str,
        file_spec: AttachmentFileSpec,
        *,
        reservation: CapacityReservation | None = None,
    ) -> AttachmentRecord:
        """把共享副本关联到现有逻辑附件，不重复计算物理占用。"""
        file = self._materialize_files([file_spec])[0]
        with self._lock:
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT status FROM managed_attachments WHERE id=?",
                    (attachment_id,),
                ).fetchone()
                if row is None or row["status"] == "deleted":
                    raise KeyError(attachment_id)
                existing = connection.execute(
                    "SELECT 1 FROM attachment_files WHERE path=?",
                    (file.relative_path,),
                ).fetchone()
                new_bytes = 0 if existing is not None else file.bytes
                reserved_by_others = sum(self._reservations.values())
                if reservation is not None:
                    reserved_by_others -= reservation.bytes
                managed = self._managed_bytes_on(connection)
                if (
                    self._max_bytes is not None
                    and managed + reserved_by_others + new_bytes > self._max_bytes
                ):
                    raise AttachmentCapacityError(
                        "Attachment storage limit reached; delete managed attachments before adding more"
                    )
                if existing is None:
                    connection.execute(
                        """INSERT INTO attachment_files
                           (path, bytes, role, mime, sha256) VALUES (?, ?, ?, ?, ?)""",
                        (
                            file.relative_path,
                            file.bytes,
                            file.role,
                            file.mime,
                            file.sha256,
                        ),
                    )
                connection.execute(
                    """INSERT OR IGNORE INTO attachment_file_links
                       (attachment_id, file_path) VALUES (?, ?)""",
                    (attachment_id, file.relative_path),
                )
                if reservation is not None:
                    self._reservations.pop(reservation.token, None)
                    reservation._released = True
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
            finally:
                connection.close()
            return self.get(attachment_id)

    def get(self, attachment_id: str) -> AttachmentRecord:
        with self._lock:
            connection = self._connect()
            try:
                record = self._load_record(connection, attachment_id)
            finally:
                connection.close()
        if record is None:
            raise KeyError(attachment_id)
        return record

    def get_many(self, attachment_ids: Iterable[str]) -> dict[str, AttachmentRecord]:
        wanted = tuple(dict.fromkeys(attachment_ids))
        if not wanted:
            return {}
        with self._lock:
            connection = self._connect()
            try:
                records = self._load_records(connection, wanted)
            finally:
                connection.close()
        return {record.id: record for record in records}

    def find_by_paths(self, paths: Iterable[Path]) -> dict[str, AttachmentRecord]:
        relative = [self._relative_path(path, require_file=False) for path in paths]
        if not relative:
            return {}
        with self._lock:
            connection = self._connect()
            try:
                rows = connection.execute(
                    f"""SELECT DISTINCT attachment_id FROM attachment_file_links
                        WHERE file_path IN ({_placeholders(relative)})""",
                    tuple(relative),
                ).fetchall()
                records = self._load_records(
                    connection, tuple(row["attachment_id"] for row in rows),
                )
            finally:
                connection.close()
        by_path: dict[str, AttachmentRecord] = {}
        for record in records:
            for file in record.files:
                by_path[file.relative_path] = record
        return {
            relative_path: by_path[relative_path]
            for relative_path in relative
            if relative_path in by_path
        }

    def attachment_for_path(self, path: Path) -> AttachmentRecord | None:
        relative = self._relative_path(path, require_file=False)
        return self.find_by_paths([Path(relative)]).get(relative)

    def relative_path(self, path: Path, *, require_file: bool = False) -> str:
        """返回实例内相对路径，同时执行托管根与符号链接校验。"""
        return self._relative_path(path, require_file=require_file)

    def resolve_relative(self, relative_path: str) -> Path:
        """把数据库中的受限相对路径还原为实际路径。"""
        return self._target_path(relative_path)

    def records_for_path(self, path: Path) -> list[AttachmentRecord]:
        """返回共享同一物理路径的全部逻辑附件。"""
        relative = self._relative_path(path, require_file=False)
        with self._lock:
            connection = self._connect()
            try:
                rows = connection.execute(
                    """SELECT DISTINCT attachment_id FROM attachment_file_links
                       WHERE file_path=? ORDER BY attachment_id""",
                    (relative,),
                ).fetchall()
                records = self._load_records(
                    connection, tuple(row["attachment_id"] for row in rows),
                )
            finally:
                connection.close()
        return list(records)

    def find_by_metadata(
        self, key: str, value: object, *, include_deleted: bool = False,
    ) -> list[AttachmentRecord]:
        """按小型 metadata 字段查找附件，用于副本与来源关联。"""
        with self._lock:
            connection = self._connect()
            try:
                where = "" if include_deleted else "WHERE status != 'deleted'"
                rows = connection.execute(
                    f"""SELECT id, metadata FROM managed_attachments
                        {where} ORDER BY created_at DESC""",
                ).fetchall()
                ids = [
                    row["id"]
                    for row in rows
                    if _decode_json(row["metadata"]).get(key) == value
                ]
                records = self._load_records(connection, ids)
            finally:
                connection.close()
        return list(records)

    def list_active(self) -> list[AttachmentRecord]:
        with self._lock:
            connection = self._connect()
            try:
                rows = connection.execute(
                    """SELECT id FROM managed_attachments
                       WHERE status != 'deleted' ORDER BY created_at DESC, id"""
                ).fetchall()
                records = self._load_records(connection, tuple(row["id"] for row in rows))
            finally:
                connection.close()
        return list(records)

    def attachments_for_sessions(self, session_keys: Iterable[str]) -> list[AttachmentRecord]:
        keys = tuple(dict.fromkeys(session_keys))
        if not keys:
            return []
        with self._lock:
            connection = self._connect()
            try:
                rows = connection.execute(
                    f"""SELECT DISTINCT attachment_id FROM attachment_references
                        WHERE session_key IN ({_placeholders(keys)})""",
                    keys,
                ).fetchall()
                records = self._load_records(
                    connection, tuple(row["attachment_id"] for row in rows),
                )
            finally:
                connection.close()
        return list(records)

    def replace_references(
        self, references: Mapping[str, Iterable[AttachmentReference]],
    ) -> None:
        """用完整盘点结果重算会话引用，并维护无引用起点。"""
        now = _now_iso()
        with self._lock:
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                active_ids = {
                    row["id"]
                    for row in connection.execute(
                        "SELECT id FROM managed_attachments WHERE status != 'deleted'"
                    )
                }
                for attachment_id in active_ids:
                    desired = tuple(sorted(
                        set(references.get(attachment_id, ())),
                        key=lambda item: item.key,
                    ))
                    current_count = connection.execute(
                        "SELECT COUNT(*) FROM attachment_references WHERE attachment_id=?",
                        (attachment_id,),
                    ).fetchone()[0]
                    connection.execute(
                        "DELETE FROM attachment_references WHERE attachment_id=?",
                        (attachment_id,),
                    )
                    connection.executemany(
                        """INSERT INTO attachment_references
                           (attachment_id, session_key, title) VALUES (?, ?, ?)""",
                        [(attachment_id, item.key, item.title) for item in desired],
                    )
                    if desired:
                        connection.execute(
                            "UPDATE managed_attachments SET unreferenced_since=NULL WHERE id=?",
                            (attachment_id,),
                        )
                    elif current_count or connection.execute(
                        "SELECT unreferenced_since FROM managed_attachments WHERE id=?",
                        (attachment_id,),
                    ).fetchone()[0] is None:
                        connection.execute(
                            "UPDATE managed_attachments SET unreferenced_since=? WHERE id=?",
                            (now, attachment_id),
                        )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
            finally:
                connection.close()

    def usage(self, *, pending_bytes: int = 0) -> AttachmentUsage:
        with self._lock:
            connection = self._connect()
            try:
                managed = self._managed_bytes_on(connection)
                registered = {
                    row["path"]
                    for row in connection.execute(
                        """SELECT DISTINCT f.path
                           FROM attachment_files f
                           JOIN attachment_file_links l ON l.file_path=f.path
                           JOIN managed_attachments a ON a.id=l.attachment_id
                           WHERE a.status != 'deleted'""",
                    )
                }
            finally:
                connection.close()
            protected = self._protected_bytes(registered)
        return AttachmentUsage(
            managed_bytes=managed,
            pending_bytes=max(0, pending_bytes),
            protected_bytes=protected,
        )

    def preview_delete(self, attachment_ids: Iterable[str]) -> AttachmentDeletePreview:
        ids = tuple(dict.fromkeys(attachment_ids))
        with self._lock:
            connection = self._connect()
            try:
                records = self._load_records(connection, ids)
                return self._preview_delete_on(connection, ids, records)
            finally:
                connection.close()

    def delete(self, attachment_ids: Iterable[str]) -> AttachmentDeleteResult:
        """按稳定 ID 删除附件；仅删除没有其他有效逻辑附件共享的文件。"""
        ids = tuple(dict.fromkeys(attachment_ids))
        if not ids:
            return AttachmentDeleteResult((), (), 0, ())
        with self._lock:
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                records = self._load_records(connection, ids)
                preview = self._preview_delete_on(connection, ids, records)
                blocked = {str(item["id"]): str(item["reason"]) for item in preview.blocked}
                planned = [
                    record for record in records
                    if record.id not in blocked and record.status != "deleted"
                ]
                selected = {record.id for record in planned}
                file_records: dict[str, list[AttachmentFile]] = {}
                for record in planned:
                    for file in record.files:
                        file_records.setdefault(file.relative_path, []).append(file)

                failed: dict[str, str] = {}
                freed = 0
                for relative_path, files in file_records.items():
                    other_links = connection.execute(
                        """SELECT DISTINCT l.attachment_id
                           FROM attachment_file_links l
                           JOIN managed_attachments a ON a.id=l.attachment_id
                           WHERE l.file_path=? AND a.status != 'deleted'""",
                        (relative_path,),
                    ).fetchall()
                    if any(row["attachment_id"] not in selected for row in other_links):
                        continue
                    try:
                        target = self._target_path(relative_path)
                        if target.is_symlink():
                            raise AttachmentPathError("managed file is a symlink")
                        if target.exists():
                            actual = target.stat().st_size
                            target.unlink()
                            freed += actual
                    except (OSError, AttachmentPathError) as exc:
                        for record in planned:
                            if any(file.relative_path == relative_path for file in record.files):
                                failed[record.id] = f"Could not delete managed file: {exc}"

                affected = preview.affected_sessions
                removed: list[str] = []
                for record in planned:
                    record_failed = record.id in failed or any(
                        file.relative_path in failed.get(record.id, "")
                        for file in record.files
                    )
                    if record_failed:
                        status = self._current_status_on(connection, record.id)
                        connection.execute(
                            "UPDATE managed_attachments SET status=? WHERE id=?",
                            (status, record.id),
                        )
                    else:
                        connection.execute(
                            "UPDATE managed_attachments SET status='deleted' WHERE id=?",
                            (record.id,),
                        )
                        removed.append(record.id)
                for attachment_id, reason in failed.items():
                    if attachment_id not in removed:
                        connection.execute(
                            "UPDATE managed_attachments SET status=? WHERE id=?",
                            (self._current_status_on(connection, attachment_id), attachment_id),
                        )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
            finally:
                connection.close()
        failures = tuple(
            {"id": attachment_id, "reason": reason}
            for attachment_id, reason in blocked.items()
        ) + tuple(
            {"id": attachment_id, "reason": reason}
            for attachment_id, reason in failed.items()
        )
        return AttachmentDeleteResult(
            deleted=tuple(removed),
            failed=failures,
            freed_bytes=freed,
            affected_sessions=affected,
        )

    def cleanup_orphans(
        self,
        *,
        orphan_days: int,
        now: datetime | None = None,
    ) -> AttachmentDeleteResult:
        """删除超过指定天数仍无会话引用的附件。"""
        if isinstance(orphan_days, bool) or orphan_days <= 0:
            raise ValueError("orphan_days must be a positive integer")
        current = now or datetime.now(timezone.utc)
        cutoff = (current - timedelta(days=orphan_days)).astimezone(timezone.utc).isoformat()
        with self._lock:
            connection = self._connect()
            try:
                rows = connection.execute(
                    """SELECT id FROM managed_attachments
                       WHERE status='available' AND unreferenced_since IS NOT NULL
                         AND unreferenced_since <= ?
                         AND NOT EXISTS (
                           SELECT 1 FROM attachment_references r
                           WHERE r.attachment_id=managed_attachments.id
                         )
                       ORDER BY unreferenced_since, id""",
                    (cutoff,),
                ).fetchall()
            finally:
                connection.close()
        if not rows:
            return AttachmentDeleteResult((), (), 0, ())
        return self.delete(tuple(row["id"] for row in rows))

    def status_for_ids(self, attachment_ids: Iterable[str]) -> dict[str, AttachmentStatus]:
        records = self.get_many(attachment_ids)
        return {attachment_id: record.status for attachment_id, record in records.items()}

    def reconcile_statuses(self) -> None:
        """按实际文件状态更新 available/missing，不删除任何文件。"""
        with self._lock:
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                rows = connection.execute(
                    """SELECT id, status FROM managed_attachments WHERE status != 'deleted'"""
                ).fetchall()
                for row in rows:
                    status = self._current_status_on(connection, row["id"])
                    if status != row["status"]:
                        connection.execute(
                            "UPDATE managed_attachments SET status=? WHERE id=?",
                            (status, row["id"]),
                        )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
            finally:
                connection.close()

    def _initialize(self) -> None:
        with self._lock:
            connection = sqlite3.connect(self.db_path, timeout=30)
            try:
                connection.execute("PRAGMA journal_mode=WAL")
                connection.execute("PRAGMA synchronous=FULL")
                connection.executescript(
                    """
                    PRAGMA foreign_keys=ON;
                    CREATE TABLE IF NOT EXISTS managed_attachments (
                        id TEXT PRIMARY KEY,
                        name TEXT NOT NULL,
                        kind TEXT NOT NULL,
                        source TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        status TEXT NOT NULL,
                        unreferenced_since TEXT,
                        owner_session_key TEXT,
                        metadata TEXT NOT NULL DEFAULT '{}'
                    );
                    CREATE TABLE IF NOT EXISTS attachment_files (
                        path TEXT PRIMARY KEY,
                        bytes INTEGER NOT NULL,
                        role TEXT NOT NULL,
                        mime TEXT,
                        sha256 TEXT
                    );
                    CREATE TABLE IF NOT EXISTS attachment_file_links (
                        attachment_id TEXT NOT NULL REFERENCES managed_attachments(id) ON DELETE CASCADE,
                        file_path TEXT NOT NULL REFERENCES attachment_files(path) ON DELETE CASCADE,
                        PRIMARY KEY (attachment_id, file_path)
                    );
                    CREATE TABLE IF NOT EXISTS attachment_references (
                        attachment_id TEXT NOT NULL REFERENCES managed_attachments(id) ON DELETE CASCADE,
                        session_key TEXT NOT NULL,
                        title TEXT NOT NULL,
                        PRIMARY KEY (attachment_id, session_key)
                    );
                    CREATE INDEX IF NOT EXISTS managed_attachments_orphans
                        ON managed_attachments(status, unreferenced_since);
                    CREATE INDEX IF NOT EXISTS attachment_references_session
                        ON attachment_references(session_key);
                    """
                )
            finally:
                connection.close()
            with _suppress_os_error():
                os.chmod(self.db_path, 0o600)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    def _materialize_files(self, files: Iterable[AttachmentFileSpec]) -> tuple[AttachmentFile, ...]:
        materialized: list[AttachmentFile] = []
        seen: set[str] = set()
        for file in files:
            relative = self._relative_path(file.path, require_file=True)
            if relative in seen:
                raise AttachmentPathError("duplicate managed file path")
            seen.add(relative)
            size = file.path.stat().st_size
            materialized.append(
                AttachmentFile(
                    relative_path=relative,
                    bytes=size,
                    role=file.role,
                    mime=file.mime,
                    sha256=file.sha256,
                )
            )
        if not materialized:
            raise ValueError("managed attachment requires at least one file")
        return tuple(materialized)

    def _relative_path(self, path: Path, *, require_file: bool) -> str:
        candidate = path.expanduser()
        if not candidate.is_absolute():
            candidate = self.data_dir / candidate
        lexical = candidate.absolute()
        try:
            lexical_relative = lexical.relative_to(self.data_dir)
        except ValueError:
            lexical_relative = None
        if lexical_relative is not None:
            current = self.data_dir
            for part in lexical_relative.parts:
                current = current / part
                if current.is_symlink():
                    raise AttachmentPathError("managed file path contains a symlink")
        resolved = candidate.resolve(strict=False)
        try:
            relative = resolved.relative_to(self.data_dir)
        except ValueError as exc:
            raise AttachmentPathError("managed file must stay inside the instance data directory") from exc
        pure = PurePosixPath(relative.as_posix())
        if not pure.parts or pure.parts[0] not in _MANAGED_ROOTS:
            raise AttachmentPathError("file is outside managed attachment roots")
        current = self.data_dir
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                raise AttachmentPathError("managed file path contains a symlink")
        if require_file and not candidate.is_file():
            raise FileNotFoundError(candidate)
        return pure.as_posix()

    def _target_path(self, relative_path: str) -> Path:
        pure = PurePosixPath(relative_path)
        if pure.is_absolute() or not pure.parts or pure.parts[0] not in _MANAGED_ROOTS:
            raise AttachmentPathError("stored managed path is outside allowed roots")
        if any(part in {"", ".", ".."} for part in pure.parts):
            raise AttachmentPathError("stored managed path is invalid")
        target = self.data_dir.joinpath(*pure.parts)
        self._check_no_symlink_ancestors(target)
        return target

    def _check_no_symlink_ancestors(self, target: Path) -> None:
        current = self.data_dir
        for part in target.relative_to(self.data_dir).parts:
            current = current / part
            if current.is_symlink():
                raise AttachmentPathError("managed file path contains a symlink")

    def _managed_bytes_locked(self) -> int:
        connection = self._connect()
        try:
            return self._managed_bytes_on(connection)
        finally:
            connection.close()

    def _managed_bytes_on(self, connection: sqlite3.Connection) -> int:
        rows = connection.execute(
            """SELECT DISTINCT f.path, f.bytes FROM attachment_files f
               JOIN attachment_file_links l ON l.file_path=f.path
               JOIN managed_attachments a ON a.id=l.attachment_id
               WHERE a.status != 'deleted'"""
        ).fetchall()
        total = 0
        for row in rows:
            try:
                target = self._target_path(row["path"])
                if target.is_file():
                    total += target.stat().st_size
            except (OSError, AttachmentPathError):
                continue
        return total

    def _protected_bytes(self, registered: set[str]) -> int:
        total = 0
        pending_names = {
            marker.name
            for marker in (self.data_dir / "media" / "websocket" / ".pending-attachments").glob("*")
            if marker.is_file()
        } if (self.data_dir / "media" / "websocket" / ".pending-attachments").is_dir() else set()
        for root_name in _MANAGED_ROOTS:
            root = self.data_dir / root_name
            if not root.is_dir() or root.is_symlink():
                continue
            for path in root.rglob("*"):
                if path.is_symlink() or not path.is_file():
                    continue
                if ".pending-attachments" in path.parts:
                    continue
                if path.name in pending_names and path.parent == self.data_dir / "media" / "websocket":
                    continue
                relative = path.relative_to(self.data_dir).as_posix()
                if relative in registered:
                    continue
                try:
                    total += path.stat().st_size
                except OSError:
                    continue
        return total

    def _load_records(
        self, connection: sqlite3.Connection, attachment_ids: Iterable[str],
    ) -> tuple[AttachmentRecord, ...]:
        ids = tuple(dict.fromkeys(attachment_ids))
        if not ids:
            return ()
        rows = connection.execute(
            f"SELECT * FROM managed_attachments WHERE id IN ({_placeholders(ids)})",
            ids,
        ).fetchall()
        records = []
        for row in rows:
            record = self._record_from_row(connection, row)
            records.append(record)
        order = {attachment_id: index for index, attachment_id in enumerate(ids)}
        records.sort(key=lambda record: order[record.id])
        return tuple(records)

    def _load_record(
        self, connection: sqlite3.Connection, attachment_id: str,
    ) -> AttachmentRecord | None:
        records = self._load_records(connection, (attachment_id,))
        return records[0] if records else None

    def _record_from_row(
        self, connection: sqlite3.Connection, row: sqlite3.Row,
    ) -> AttachmentRecord:
        files = tuple(
            AttachmentFile(
                relative_path=file_row["path"],
                bytes=file_row["bytes"],
                role=file_row["role"],
                mime=file_row["mime"],
                sha256=file_row["sha256"],
            )
            for file_row in connection.execute(
                """SELECT f.* FROM attachment_files f
                   JOIN attachment_file_links l ON l.file_path=f.path
                   WHERE l.attachment_id=? ORDER BY f.path""",
                (row["id"],),
            )
        )
        references = tuple(
            AttachmentReference(key=reference_row["session_key"], title=reference_row["title"])
            for reference_row in connection.execute(
                """SELECT session_key, title FROM attachment_references
                   WHERE attachment_id=? ORDER BY session_key""",
                (row["id"],),
            )
        )
        raw_metadata = _decode_json(row["metadata"])
        return AttachmentRecord(
            id=row["id"],
            name=row["name"],
            kind=row["kind"],
            source=row["source"],
            bytes=sum(file.bytes for file in files),
            created_at=row["created_at"],
            unreferenced_since=row["unreferenced_since"],
            status=self._current_status_from_record_files(files, row["status"]),
            owner_session_key=row["owner_session_key"],
            files=files,
            references=references,
            metadata=raw_metadata,
        )

    def _current_status_from_record_files(
        self, files: tuple[AttachmentFile, ...], stored: str,
    ) -> AttachmentStatus:
        if stored == "deleted":
            return "deleted"
        for file in files:
            try:
                if self._target_path(file.relative_path).is_file():
                    return "available"
            except (OSError, AttachmentPathError):
                continue
        return "missing"

    def _current_status_on(self, connection: sqlite3.Connection, attachment_id: str) -> AttachmentStatus:
        record = self._load_record(connection, attachment_id)
        if record is None:
            return "missing"
        return record.status

    def _preview_delete_on(
        self,
        connection: sqlite3.Connection,
        requested_ids: tuple[str, ...],
        records: tuple[AttachmentRecord, ...],
    ) -> AttachmentDeletePreview:
        by_id = {record.id: record for record in records}
        blocked: list[dict[str, str]] = []
        for attachment_id in requested_ids:
            if attachment_id not in by_id:
                blocked.append({"id": attachment_id, "reason": "Unknown managed attachment"})
                continue
            record = by_id[attachment_id]
            if record.status == "deleted":
                blocked.append({"id": attachment_id, "reason": "Attachment is already deleted"})
                continue
            for file in record.files:
                try:
                    target = self._target_path(file.relative_path)
                except AttachmentPathError as exc:
                    blocked.append({"id": attachment_id, "reason": str(exc)})
                    break
                if target.is_symlink():
                    blocked.append({"id": attachment_id, "reason": "Managed file is a symlink"})
                    break
        blocked_ids = {item["id"] for item in blocked}
        selected = {record.id for record in records if record.id not in blocked_ids}
        file_paths: set[str] = set()
        for record in records:
            if record.id in selected:
                file_paths.update(file.relative_path for file in record.files)
        estimated = 0
        for relative_path in file_paths:
            links = connection.execute(
                """SELECT DISTINCT l.attachment_id
                   FROM attachment_file_links l
                   JOIN managed_attachments a ON a.id=l.attachment_id
                   WHERE l.file_path=? AND a.status != 'deleted'""",
                (relative_path,),
            ).fetchall()
            if any(row["attachment_id"] not in selected for row in links):
                continue
            try:
                target = self._target_path(relative_path)
                if target.is_file() and not target.is_symlink():
                    estimated += target.stat().st_size
            except (OSError, AttachmentPathError):
                continue
        affected = _merged_references(
            reference
            for record in records
            if record.id in selected
            for reference in record.references
        )
        items = tuple(
            {
                "id": record.id,
                "name": record.name,
                "bytes": record.bytes,
                "files": [file.relative_path for file in record.files],
            }
            for record in records
            if record.id in selected
        )
        return AttachmentDeletePreview(
            items=items,
            affected_sessions=affected,
            estimated_freed_bytes=estimated,
            blocked=tuple(blocked),
        )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _encode_json(value: Mapping[str, object]) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def _decode_json(value: str) -> dict[str, object]:
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _placeholders(values: Iterable[object]) -> str:
    return ",".join("?" for _ in values)


def _merged_references(references: Iterable[AttachmentReference]) -> tuple[AttachmentReference, ...]:
    by_key: dict[str, AttachmentReference] = {}
    for reference in references:
        by_key[reference.key] = reference
    return tuple(by_key[key] for key in sorted(by_key))


class _suppress_os_error:
    def __enter__(self) -> None:
        return None

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> bool:
        return isinstance(exc, OSError)
