"""Durable source documents waiting for Dream consolidation."""

from __future__ import annotations

import base64
import binascii
import json
import re
import shutil
import stat
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, cast
from uuid import uuid4

MAX_IMPORT_BYTES = 262_144
_MAX_IMPORT_BASE64_CHARS = 4 * ((MAX_IMPORT_BYTES + 2) // 3)
_IMPORT_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_ENTRY_FILE = "entry.json"
_CONTENT_FILE = "content.md"
_ImportStatus = Literal["pending", "failed", "archived"]


def is_path_link(path: Path) -> bool:
    """Return whether a managed path is a link or Windows reparse point."""
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    except OSError:
        return True
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    )


class MemoryImportError(ValueError):
    """A user-facing import failure with an HTTP-compatible status."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


@dataclass(frozen=True)
class _ImportMetadata:
    name: str
    size: int
    imported_at: str
    error: str | None = None
    archived_at: str | None = None

    def to_json(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "name": self.name,
            "bytes": self.size,
            "imported_at": self.imported_at,
        }
        if self.error:
            data["error"] = self.error
        if self.archived_at:
            data["archived_at"] = self.archived_at
        return data

    @classmethod
    def from_json(cls, data: object) -> _ImportMetadata | None:
        if not isinstance(data, dict):
            return None
        values = cast(dict[object, object], data)
        name = values.get("name")
        size = values.get("bytes")
        imported_at = values.get("imported_at")
        error = values.get("error")
        archived_at = values.get("archived_at")
        if (
            not isinstance(name, str)
            or not name
            or not isinstance(size, int)
            or size < 0
            or not isinstance(imported_at, str)
            or not imported_at
        ):
            return None
        if error is not None and not isinstance(error, str):
            return None
        if archived_at is not None and not isinstance(archived_at, str):
            return None
        return cls(
            name=name,
            size=size,
            imported_at=imported_at,
            error=error or None,
            archived_at=archived_at or None,
        )


class MemoryImportStore:
    """Own raw imported Markdown outside the memory wiki and Git history."""

    MAX_IMPORT_BYTES = MAX_IMPORT_BYTES

    def __init__(self, workspace: Path):
        self.workspace = Path(workspace).expanduser().resolve()
        self.root = self.workspace / "memory" / "imports"
        self.pending_dir = self.root / "pending"
        self.archive_dir = self.root / "archive"
        self._staging_dir = self.root / ".staging"
        for path in (
            self.workspace / "memory",
            self.root,
            self.pending_dir,
            self.archive_dir,
            self._staging_dir,
        ):
            self._ensure_managed_dir(path)

    def _managed_dir(self, path: Path, *, create: bool = False) -> Path:
        try:
            relative = path.relative_to(self.workspace)
        except ValueError as exc:
            raise MemoryImportError(500, "Memory import storage is unavailable") from exc
        if not self.workspace.exists():
            if not create:
                raise MemoryImportError(500, "Memory import storage is unavailable")
            self.workspace.mkdir(parents=True)
        if not self.workspace.is_dir():
            raise MemoryImportError(500, "Memory import storage is unavailable")
        current = self.workspace
        for part in relative.parts:
            current = current / part
            if is_path_link(current):
                raise MemoryImportError(500, "Memory import storage is unavailable")
            if current.exists():
                if not current.is_dir():
                    raise MemoryImportError(500, "Memory import storage is unavailable")
            elif create:
                current.mkdir()
            else:
                raise MemoryImportError(500, "Memory import storage is unavailable")
        if not current.is_dir():
            raise MemoryImportError(500, "Memory import storage is unavailable")
        return current

    def _ensure_managed_dir(self, path: Path) -> None:
        self._managed_dir(path, create=True)

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

    @staticmethod
    def _display_name(value: object) -> str:
        if not isinstance(value, str):
            raise MemoryImportError(400, "File name must be a string")
        name = value
        if (
            not name
            or name in {".", ".."}
            or "/" in name
            or "\\" in name
            or "\x00" in name
            or any(ord(char) < 32 for char in name)
            or len(name) > 255
        ):
            raise MemoryImportError(400, "File name must be a Markdown basename")
        if not name.lower().endswith(".md"):
            raise MemoryImportError(400, "Only Markdown files can be imported")
        return name

    @staticmethod
    def _decode_import(name: object, content_base64: object) -> tuple[str, bytes]:
        display_name = MemoryImportStore._display_name(name)
        if not isinstance(content_base64, str):
            raise MemoryImportError(400, "File content must be Base64 text")
        if len(content_base64) > _MAX_IMPORT_BASE64_CHARS:
            raise MemoryImportError(413, "Imported file is too large")
        try:
            data = base64.b64decode(content_base64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise MemoryImportError(400, "File content is not valid Base64") from exc
        if len(data) > MAX_IMPORT_BYTES:
            raise MemoryImportError(413, "Imported file is too large")
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise MemoryImportError(400, "Imported file must be UTF-8 Markdown") from exc
        if not text or "\x00" in text:
            raise MemoryImportError(400, "Imported file must contain UTF-8 text")
        return display_name, data

    @staticmethod
    def _write_metadata(directory: Path, metadata: _ImportMetadata) -> None:
        fd, temporary_name = tempfile.mkstemp(
            prefix=".entry-",
            suffix=".tmp",
            dir=directory,
        )
        temporary = Path(temporary_name)
        try:
            with open(fd, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(metadata.to_json(), handle, ensure_ascii=False, separators=(",", ":"))
                handle.write("\n")
                handle.flush()
            temporary.replace(directory / _ENTRY_FILE)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _read_metadata(directory: Path) -> _ImportMetadata | None:
        if is_path_link(directory / _ENTRY_FILE):
            return None
        try:
            data = json.loads((directory / _ENTRY_FILE).read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None
        return _ImportMetadata.from_json(data)

    @staticmethod
    def _entry_payload(
        import_id: str,
        metadata: _ImportMetadata,
        *,
        archived: bool,
    ) -> dict[str, Any]:
        status: _ImportStatus = "archived" if archived else (
            "failed" if metadata.error else "pending"
        )
        entry: dict[str, Any] = {
            "id": import_id,
            "name": metadata.name,
            "bytes": metadata.size,
            "imported_at": metadata.imported_at,
            "status": status,
        }
        if not archived and metadata.error:
            entry["error"] = metadata.error
        if archived and metadata.archived_at:
            entry["archived_at"] = metadata.archived_at
        return entry

    @staticmethod
    def _valid_id(import_id: object) -> str | None:
        if not isinstance(import_id, str) or _IMPORT_ID_RE.fullmatch(import_id) is None:
            return None
        return import_id

    def _safe_entry_dir(self, root: Path, import_id: str) -> Path | None:
        try:
            root = self._managed_dir(root)
        except MemoryImportError:
            return None
        candidate = root / import_id
        if is_path_link(candidate):
            return None
        try:
            candidate.resolve(strict=False).relative_to(root.resolve(strict=True))
        except (OSError, ValueError):
            return None
        return candidate

    def _iter_entries(
        self,
        root: Path,
        *,
        archived: bool,
    ) -> list[tuple[dict[str, Any], Path, _ImportMetadata]]:
        entries: list[tuple[dict[str, Any], Path, _ImportMetadata]] = []
        try:
            root = self._managed_dir(root)
        except MemoryImportError:
            return entries
        try:
            children = list(root.iterdir())
        except OSError:
            return entries
        for directory in children:
            import_id = self._valid_id(directory.name)
            if import_id is None or is_path_link(directory) or not directory.is_dir():
                continue
            metadata = self._read_metadata(directory)
            content = directory / _CONTENT_FILE
            if metadata is None or is_path_link(content) or not content.is_file():
                continue
            entries.append((
                self._entry_payload(import_id, metadata, archived=archived),
                directory,
                metadata,
            ))
        entries.sort(key=lambda item: (item[0]["imported_at"], item[0]["id"]))
        return entries

    def import_bytes(self, name: object, content_base64: object) -> dict[str, Any]:
        """Persist one validated raw Markdown document and return its entry."""
        display_name, data = self._decode_import(name, content_base64)
        self._ensure_managed_dir(self._staging_dir)
        self._ensure_managed_dir(self.pending_dir)
        import_id = uuid4().hex
        metadata = _ImportMetadata(
            name=display_name,
            size=len(data),
            imported_at=self._now(),
        )
        staging = Path(tempfile.mkdtemp(prefix=".import-", dir=self._staging_dir))
        target = self.pending_dir / import_id
        try:
            (staging / _CONTENT_FILE).write_bytes(data)
            self._write_metadata(staging, metadata)
            staging.rename(target)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        return self._entry_payload(import_id, metadata, archived=False)

    def list_entries(self) -> list[dict[str, Any]]:
        pending = [entry for entry, _, _ in self._iter_entries(self.pending_dir, archived=False)]
        archived = [entry for entry, _, _ in self._iter_entries(self.archive_dir, archived=True)]
        return [*pending, *archived]

    def retryable_ids(self) -> list[str]:
        return [
            cast(str, entry["id"])
            for entry, _, _ in self._iter_entries(self.pending_dir, archived=False)
        ]

    def read_import(self, import_id: object) -> tuple[dict[str, Any], str]:
        valid_id = self._valid_id(import_id)
        if valid_id is None:
            raise MemoryImportError(404, "Imported file not found")
        for root, archived in ((self.pending_dir, False), (self.archive_dir, True)):
            directory = self._safe_entry_dir(root, valid_id)
            if directory is None or not directory.is_dir():
                continue
            metadata = self._read_metadata(directory)
            content_path = directory / _CONTENT_FILE
            if (
                metadata is None
                or is_path_link(content_path)
                or not content_path.is_file()
            ):
                continue
            try:
                content = content_path.read_bytes().decode("utf-8-sig")
            except (OSError, UnicodeDecodeError) as exc:
                raise MemoryImportError(500, "Imported file could not be read") from exc
            return self._entry_payload(valid_id, metadata, archived=archived), content
        raise MemoryImportError(404, "Imported file not found")

    def read_pending(self, import_id: str) -> tuple[dict[str, Any], str]:
        entry, content = self.read_import(import_id)
        if entry["status"] == "archived":
            raise MemoryImportError(404, "Imported file not found")
        return entry, content

    def archive(self, import_id: str) -> dict[str, Any]:
        valid_id = self._valid_id(import_id)
        if valid_id is None:
            raise MemoryImportError(404, "Imported file not found")
        pending = self._safe_entry_dir(self.pending_dir, valid_id)
        if pending is None or not pending.is_dir():
            raise MemoryImportError(404, "Imported file not found")
        metadata = self._read_metadata(pending)
        if metadata is None:
            raise MemoryImportError(404, "Imported file not found")
        archive_root = self._managed_dir(self.archive_dir)
        archived = _ImportMetadata(
            name=metadata.name,
            size=metadata.size,
            imported_at=metadata.imported_at,
            archived_at=self._now(),
        )
        try:
            self._write_metadata(pending, archived)
            target = archive_root / valid_id
            if target.exists() or is_path_link(target):
                raise MemoryImportError(409, "Imported file is already archived")
            pending.rename(target)
        except MemoryImportError:
            raise
        except OSError as exc:
            raise MemoryImportError(500, "Imported file could not be archived") from exc
        return self._entry_payload(valid_id, archived, archived=True)

    def mark_failed(self, import_id: str, error: str) -> dict[str, Any]:
        valid_id = self._valid_id(import_id)
        if valid_id is None:
            raise MemoryImportError(404, "Imported file not found")
        directory = self._safe_entry_dir(self.pending_dir, valid_id)
        if directory is None or not directory.is_dir():
            raise MemoryImportError(404, "Imported file not found")
        metadata = self._read_metadata(directory)
        if metadata is None:
            raise MemoryImportError(404, "Imported file not found")
        message = " ".join(error.split())[:240] or "Dream could not process this file"
        failed = _ImportMetadata(
            name=metadata.name,
            size=metadata.size,
            imported_at=metadata.imported_at,
            error=message,
        )
        try:
            self._write_metadata(directory, failed)
        except OSError as exc:
            raise MemoryImportError(500, "Imported file status could not be saved") from exc
        return self._entry_payload(valid_id, failed, archived=False)

    def delete_archived(self, ids: object) -> dict[str, list[str]]:
        if not isinstance(ids, list):
            raise MemoryImportError(400, "Invalid imported file IDs")
        raw_ids = cast(list[object], ids)
        if len(raw_ids) > 1000:
            raise MemoryImportError(400, "Invalid imported file IDs")
        deleted: list[str] = []
        failed: list[str] = []
        seen: set[str] = set()
        for raw_id in raw_ids:
            import_id = self._valid_id(raw_id)
            if import_id is None:
                if isinstance(raw_id, str):
                    failed.append(raw_id)
                continue
            if import_id in seen:
                continue
            seen.add(import_id)
            directory = self._safe_entry_dir(self.archive_dir, import_id)
            if directory is None or is_path_link(directory) or not directory.is_dir():
                failed.append(import_id)
                continue
            try:
                archive_root = self._managed_dir(self.archive_dir)
                resolved = directory.resolve(strict=True)
                resolved.relative_to(archive_root.resolve(strict=True))
                shutil.rmtree(directory)
            except (MemoryImportError, OSError, ValueError):
                failed.append(import_id)
                continue
            deleted.append(import_id)
        return {"deleted": deleted, "failed": failed}
