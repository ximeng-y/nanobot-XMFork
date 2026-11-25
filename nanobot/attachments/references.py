"""从完整持久会话记录中提取托管附件引用。"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from typing import cast

_IMAGE_ID = re.compile(r"[0-9a-f]{64}")
_MARKDOWN_IMAGE = re.compile(r"!\[[^\]]*\]\(\s*(?:<([^>]+)>|([^)\s]+))")
_WORKSPACE_ID = re.compile(r"[0-9a-f]{32}")


@dataclass(frozen=True)
class SessionAttachmentReferences:
    """一个会话在全部持久记录中的附件路径与托管 ID。"""

    key: str
    title: str
    paths: frozenset[str] = frozenset()
    managed_ids: frozenset[str] = frozenset()


@dataclass
class AttachmentReferenceScan:
    """所有 workspace namespace 的完整引用盘点结果。"""

    sessions: dict[str, SessionAttachmentReferences] = field(default_factory=dict)
    borrowed_paths: set[str] = field(default_factory=set)
    complete: bool = True

    @property
    def paths(self) -> set[str]:
        return {path for refs in self.sessions.values() for path in refs.paths}

    @property
    def managed_ids(self) -> set[str]:
        return {attachment_id for refs in self.sessions.values() for attachment_id in refs.managed_ids}


def scan_session_references(
    sessions_root: Path,
    *,
    data_dir: Path | None = None,
) -> AttachmentReferenceScan:
    """只读扫描 sessions 根下每个现有 workspace 数据库。"""
    root = sessions_root.expanduser().resolve(strict=False)
    result = AttachmentReferenceScan()
    if not root.is_dir() or root.is_symlink():
        return result
    for namespace in root.iterdir():
        if (
            namespace.is_symlink()
            or not namespace.is_dir()
            or _WORKSPACE_ID.fullmatch(namespace.name) is None
        ):
            continue
        database = namespace / "sessions.sqlite3"
        if database.is_symlink() or not database.is_file():
            continue
        scanned = _scan_database(
            database,
            borrowed=result.borrowed_paths,
            data_dir=data_dir,
        )
        if scanned is None:
            result.complete = False
            continue
        result.sessions.update(scanned)
    return result


def references_from_record(
    record: object,
    *,
    data_dir: Path | None = None,
) -> tuple[set[str], set[str], set[str]]:
    """从明确媒体字段提取路径、托管 ID 和借用原路径。"""
    paths: set[str] = set()
    managed_ids: set[str] = set()
    borrowed: set[str] = set()
    _walk_value(
        record,
        paths=paths,
        managed_ids=managed_ids,
        borrowed=borrowed,
        data_dir=data_dir,
    )
    return paths, managed_ids, borrowed


def _scan_database(
    database: Path,
    *,
    borrowed: set[str],
    data_dir: Path | None,
) -> dict[str, SessionAttachmentReferences] | None:
    uri = database.resolve(strict=True).as_uri() + "?mode=ro"
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(uri, uri=True, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        sessions: dict[str, SessionAttachmentReferences] = {}
        rows = connection.execute("SELECT key, metadata FROM sessions").fetchall()
        for row in rows:
            key = str(row["key"])
            title = _session_title(key, _json_object(row["metadata"]))
            paths: set[str] = set()
            managed_ids: set[str] = set()
            for sql in (
                "SELECT record FROM messages WHERE session_key=?",
                "SELECT record FROM history_entries WHERE session_key=?",
                "SELECT record FROM checkpoints WHERE session_key=?",
                "SELECT record FROM pending_inputs WHERE session_key=?",
            ):
                try:
                    records = connection.execute(sql, (key,)).fetchall()
                except sqlite3.OperationalError:
                    continue
                for record_row in records:
                    raw = record_row["record"]
                    if not isinstance(raw, str):
                        continue
                    parsed = _json_value(raw)
                    found_paths, found_ids, found_borrowed = references_from_record(
                        parsed, data_dir=data_dir,
                    )
                    paths.update(found_paths)
                    managed_ids.update(found_ids)
                    borrowed.update(found_borrowed)
            sessions[key] = SessionAttachmentReferences(
                key=key,
                title=title,
                paths=frozenset(paths),
                managed_ids=frozenset(managed_ids),
            )
        connection.rollback()
        return sessions
    except (OSError, sqlite3.Error):
        return None
    finally:
        if connection is not None:
            connection.close()


def _session_title(key: str, metadata: dict[str, object]) -> str:
    title = metadata.get("title")
    return title.strip() if isinstance(title, str) and title.strip() else key


def _walk_value(
    value: object,
    *,
    paths: set[str],
    managed_ids: set[str],
    borrowed: set[str],
    data_dir: Path | None,
) -> None:
    if isinstance(value, dict):
        data = cast(dict[object, object], value)
        local_media = data.get("local_media_paths")
        if isinstance(local_media, list):
            borrowed.update(item for item in local_media if isinstance(item, str) and item)
        for key in ("media", "media_paths"):
            item = data.get(key)
            if isinstance(item, list):
                paths.update(path for path in item if isinstance(path, str) and path)
        for key in ("managed_id", "source_managed_id"):
            item = data.get(key)
            if isinstance(item, str) and item:
                managed_ids.add(item)
        if data.get("type") == "image_attachment":
            image_id = data.get("id")
            if isinstance(image_id, str) and _IMAGE_ID.fullmatch(image_id):
                paths.add(str(_snapshot_path(image_id, data_dir)))
        if data.get("type") == "image_url":
            image = data.get("image_url")
            url = image.get("url") if isinstance(image, dict) else None
            if isinstance(url, str) and url.startswith("data:image/"):
                paths.add(str(_snapshot_path(
                    sha256(url.encode("ascii")).hexdigest(), data_dir,
                )))
        artifacts = data.get("artifacts")
        if isinstance(artifacts, list):
            for artifact in artifacts:
                if not isinstance(artifact, dict):
                    continue
                artifact_data = cast(dict[object, object], artifact)
                path = artifact_data.get("path")
                if isinstance(path, str) and path:
                    paths.add(path)
                metadata_path = artifact_data.get("metadata_path")
                if isinstance(metadata_path, str) and metadata_path:
                    paths.add(metadata_path)
        for key in ("content", "text"):
            item = data.get(key)
            if isinstance(item, str):
                paths.update(_markdown_paths(item))
                parsed = _json_value(item) if item.lstrip().startswith("{") else None
                if isinstance(parsed, dict):
                    _walk_value(
                        parsed,
                        paths=paths,
                        managed_ids=managed_ids,
                        borrowed=borrowed,
                        data_dir=data_dir,
                    )
            elif isinstance(item, (dict, list)):
                _walk_value(
                    item,
                    paths=paths,
                    managed_ids=managed_ids,
                    borrowed=borrowed,
                    data_dir=data_dir,
                )
        for key, item in data.items():
            if key in {
                "content", "text", "artifacts", "image_url", "media",
                "media_paths", "local_media_paths", "managed_id", "source_managed_id",
            }:
                continue
            if isinstance(item, (dict, list)):
                _walk_value(
                    item,
                    paths=paths,
                    managed_ids=managed_ids,
                    borrowed=borrowed,
                    data_dir=data_dir,
                )
    elif isinstance(value, list):
        for item in value:
            _walk_value(
                item,
                paths=paths,
                managed_ids=managed_ids,
                borrowed=borrowed,
                data_dir=data_dir,
            )


def _markdown_paths(text: str) -> set[str]:
    paths: set[str] = set()
    for match in _MARKDOWN_IMAGE.finditer(text):
        raw = match.group(1) or match.group(2)
        if not raw or raw.startswith(("http://", "https://", "data:", "/api/media/")):
            continue
        paths.add(raw)
    return paths


def _snapshot_path(image_id: str, data_dir: Path | None) -> Path:
    from nanobot.config.paths import get_data_dir

    return (data_dir or get_data_dir()) / "session-images" / image_id


def _json_value(value: object) -> object:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _json_object(value: object) -> dict[str, object]:
    parsed = _json_value(value)
    return parsed if isinstance(parsed, dict) else {}
