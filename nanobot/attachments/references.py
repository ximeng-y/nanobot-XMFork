"""从完整持久会话记录中提取托管附件引用。"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from typing import cast
from urllib.parse import unquote

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
            namespace=namespace,
            borrowed=result.borrowed_paths,
            data_dir=data_dir,
        )
        if scanned is None:
            result.complete = False
            continue
        sessions, complete = scanned
        if not complete:
            result.complete = False
        for key, incoming in sessions.items():
            existing = result.sessions.get(key)
            if existing is None:
                result.sessions[key] = incoming
                continue
            result.sessions[key] = SessionAttachmentReferences(
                key=key,
                title=existing.title,
                paths=existing.paths | incoming.paths,
                managed_ids=existing.managed_ids | incoming.managed_ids,
            )
    return result


def references_from_record(
    record: object,
    *,
    data_dir: Path | None = None,
    workspace: Path | None = None,
    unresolved: set[str] | None = None,
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
        workspace=workspace,
        unresolved=unresolved,
    )
    return paths, managed_ids, borrowed


def _scan_database(
    database: Path,
    *,
    namespace: Path,
    borrowed: set[str],
    data_dir: Path | None,
) -> tuple[dict[str, SessionAttachmentReferences], bool] | None:
    uri = database.resolve(strict=True).as_uri() + "?mode=ro"
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(uri, uri=True, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        namespace_workspace = _namespace_workspace(namespace)
        unresolved: set[str] = set()
        sessions: dict[str, SessionAttachmentReferences] = {}
        rows = connection.execute("SELECT key, metadata FROM sessions").fetchall()
        for row in rows:
            key = str(row["key"])
            metadata = _json_object(row["metadata"])
            title = _session_title(key, metadata)
            workspace = _session_workspace(metadata, namespace_workspace)
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
                        parsed,
                        data_dir=data_dir,
                        workspace=workspace,
                        unresolved=unresolved,
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
        return sessions, not unresolved
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
    workspace: Path | None,
    unresolved: set[str] | None,
) -> None:
    if isinstance(value, dict):
        data = cast(dict[object, object], value)
        local_media = data.get("local_media_paths")
        if isinstance(local_media, list):
            for item in local_media:
                if not isinstance(item, str) or not item:
                    continue
                resolved = _resolve_path(item, workspace=workspace, unresolved=unresolved)
                if resolved is not None:
                    borrowed.add(resolved)
        for key in ("media", "media_paths"):
            item = data.get(key)
            if isinstance(item, list):
                for path in item:
                    if not isinstance(path, str) or not path:
                        continue
                    resolved = _resolve_path(path, workspace=workspace, unresolved=unresolved)
                    if resolved is not None:
                        paths.add(resolved)
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
                    resolved = _resolve_path(path, workspace=workspace, unresolved=unresolved)
                    if resolved is not None:
                        paths.add(resolved)
                metadata_path = artifact_data.get("metadata_path")
                if isinstance(metadata_path, str) and metadata_path:
                    resolved = _resolve_path(
                        metadata_path,
                        workspace=workspace,
                        unresolved=unresolved,
                    )
                    if resolved is not None:
                        paths.add(resolved)
        for key in ("content", "text"):
            item = data.get(key)
            if isinstance(item, str):
                paths.update(_markdown_paths(
                    item,
                    workspace=workspace,
                    unresolved=unresolved,
                ))
                parsed = _json_value(item) if item.lstrip().startswith("{") else None
                if isinstance(parsed, dict):
                    _walk_value(
                        parsed,
                        paths=paths,
                        managed_ids=managed_ids,
                        borrowed=borrowed,
                        data_dir=data_dir,
                        workspace=workspace,
                        unresolved=unresolved,
                    )
            elif isinstance(item, (dict, list)):
                _walk_value(
                    item,
                    paths=paths,
                    managed_ids=managed_ids,
                    borrowed=borrowed,
                    data_dir=data_dir,
                    workspace=workspace,
                    unresolved=unresolved,
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
                    workspace=workspace,
                    unresolved=unresolved,
                )
    elif isinstance(value, list):
        for item in value:
            _walk_value(
                item,
                paths=paths,
                managed_ids=managed_ids,
                borrowed=borrowed,
                data_dir=data_dir,
                workspace=workspace,
                unresolved=unresolved,
            )


def _markdown_paths(
    text: str,
    *,
    workspace: Path | None,
    unresolved: set[str] | None,
) -> set[str]:
    paths: set[str] = set()
    for match in _MARKDOWN_IMAGE.finditer(text):
        raw = match.group(1) or match.group(2)
        if not raw or raw.startswith(("http://", "https://", "data:", "/api/media/", "#")):
            continue
        resolved = _resolve_path(
            unquote(raw),
            workspace=workspace,
            unresolved=unresolved,
        )
        if resolved is not None:
            paths.add(resolved)
    return paths


def _resolve_path(
    raw_path: str,
    *,
    workspace: Path | None,
    unresolved: set[str] | None,
) -> str | None:
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        if workspace is None:
            if unresolved is not None:
                unresolved.add(raw_path)
            return None
        path = workspace / path
    try:
        return str(path.resolve(strict=False))
    except OSError:
        if unresolved is not None:
            unresolved.add(raw_path)
        return None


def _namespace_workspace(namespace: Path) -> Path | None:
    marker = namespace / ".workspace"
    if marker.is_symlink() or not marker.is_file():
        return None
    try:
        value = marker.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        return None
    if not value:
        return None
    return Path(value).expanduser().resolve(strict=False)


def _session_workspace(
    metadata: dict[str, object],
    namespace_workspace: Path | None,
) -> Path | None:
    scope = metadata.get("workspace_scope")
    if isinstance(scope, dict):
        project_path = cast(dict[object, object], scope).get("project_path")
        if isinstance(project_path, str) and project_path.strip():
            return Path(project_path).expanduser().resolve(strict=False)
    return namespace_workspace


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
