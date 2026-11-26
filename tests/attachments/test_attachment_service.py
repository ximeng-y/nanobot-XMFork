from __future__ import annotations

import base64
import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest

from nanobot.attachments.config import AttachmentStorageConfig
from nanobot.attachments.references import scan_session_references
from nanobot.attachments.service import AttachmentService
from nanobot.attachments.store import AttachmentCapacityError
from nanobot.channels.websocket.attachment_store import AttachmentUploadError
from nanobot.channels.websocket.attachment_store import AttachmentStore as StagingAttachmentStore
from nanobot.config.schema import Config
from nanobot.session.image_attachments import (
    IMAGE_UNAVAILABLE,
    persist_user_image_blocks,
    replay_user_image_blocks,
)
from nanobot.utils.artifacts import store_generated_image_artifact
from nanobot.webui.media_api import sign_or_stage_media_path
from nanobot.webui.media_gateway import WebUIMediaGateway

PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+yF9kAAAAASUVORK5CYII="
)
PNG_DATA_URL = f"data:image/png;base64,{base64.b64encode(PNG_BYTES).decode('ascii')}"


def test_attachment_settings_default_and_validation() -> None:
    config = Config()
    assert config.attachments == AttachmentStorageConfig()
    assert config.attachments.max_bytes is None
    assert config.attachments.auto_cleanup is False
    assert config.attachments.orphan_days == 30
    assert Config.model_validate({
        "attachments": {
            "maxBytes": 1024,
            "autoCleanup": True,
            "orphanDays": 7,
        },
    }).attachments.max_bytes == 1024

    for value in (0, -1, True):
        with pytest.raises(ValueError):
            AttachmentStorageConfig(max_bytes=value)
    for value in (0, -1, True):
        with pytest.raises(ValueError):
            AttachmentStorageConfig(orphan_days=value)


def _service(tmp_path: Path, **kwargs: object) -> AttachmentService:
    return AttachmentService(
        data_dir=tmp_path / "data",
        sessions_root=tmp_path / "sessions",
        **kwargs,
    )


def _write_session_db(
    sessions_root: Path,
    namespace: str,
    sessions: dict[str, list[dict[str, Any]]],
    metadata: dict[str, dict[str, Any]] | None = None,
) -> Path:
    directory = sessions_root / namespace
    directory.mkdir(parents=True, exist_ok=True)
    database = directory / "sessions.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE sessions (key TEXT PRIMARY KEY, metadata TEXT NOT NULL)"
        )
        connection.execute(
            """CREATE TABLE messages (
                session_key TEXT NOT NULL,
                position INTEGER NOT NULL,
                record TEXT NOT NULL,
                PRIMARY KEY (session_key, position)
            )"""
        )
        for key, records in sessions.items():
            session_metadata = {"title": key.removeprefix("websocket:")}
            session_metadata.update((metadata or {}).get(key, {}))
            connection.execute(
                "INSERT INTO sessions (key, metadata) VALUES (?, ?)",
                (key, json.dumps(session_metadata)),
            )
            connection.executemany(
                "INSERT INTO messages (session_key, position, record) VALUES (?, ?, ?)",
                [
                    (key, index, json.dumps(record, ensure_ascii=False))
                    for index, record in enumerate(records)
                ],
            )
    return database


def _delete_session_rows(database: Path, keys: set[str]) -> None:
    with sqlite3.connect(database) as connection:
        connection.executemany(
            "DELETE FROM messages WHERE session_key=?",
            [(key,) for key in keys],
        )
        connection.executemany(
            "DELETE FROM sessions WHERE key=?",
            [(key,) for key in keys],
        )


def _upload_path(data_dir: Path, name: str = "0123456789ab.png") -> Path:
    path = data_dir / "media" / "websocket" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(PNG_BYTES)
    return path


def test_reference_scan_covers_all_persisted_record_types_and_namespaces(
    tmp_path: Path,
) -> None:
    sessions_root = tmp_path / "sessions"
    first = tmp_path / "first.png"
    second = tmp_path / "second.bin"
    third = tmp_path / "third.png"
    generated = tmp_path / "data" / "media" / "generated" / "img_123456789abc.png"
    metadata = generated.with_suffix(".json")
    borrowed = tmp_path / "data" / "media" / "websocket" / "aaaaaaaaaaaa.png"
    image_id = "c" * 64
    for path in (first, second, third, generated, metadata, borrowed):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fixture")

    directory = sessions_root / ("a" * 32)
    directory.mkdir(parents=True)
    database = directory / "sessions.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE sessions (key TEXT PRIMARY KEY, metadata TEXT NOT NULL)"
        )
        connection.execute(
            """CREATE TABLE messages (
                session_key TEXT NOT NULL, record TEXT NOT NULL
            )"""
        )
        connection.execute(
            """CREATE TABLE history_entries (
                session_key TEXT NOT NULL, record TEXT NOT NULL
            )"""
        )
        connection.execute(
            """CREATE TABLE checkpoints (
                session_key TEXT PRIMARY KEY, record TEXT NOT NULL
            )"""
        )
        connection.execute(
            """CREATE TABLE pending_inputs (
                session_key TEXT NOT NULL, record TEXT NOT NULL
            )"""
        )
        connection.execute(
            "INSERT INTO sessions VALUES (?, ?)",
            ("websocket:all-records", json.dumps({"title": "All records"})),
        )
        connection.execute(
            "INSERT INTO messages VALUES (?, ?)",
            (
                "websocket:all-records",
                json.dumps({
                    "role": "user",
                    "media": [str(first)],
                    "local_media_paths": [str(borrowed)],
                }),
            ),
        )
        connection.execute(
            "INSERT INTO history_entries VALUES (?, ?)",
            (
                "websocket:all-records",
                json.dumps({
                    "content": [{
                        "type": "image_attachment",
                        "id": image_id,
                        "managed_id": "att_history",
                    }],
                }),
            ),
        )
        connection.execute(
            "INSERT INTO checkpoints VALUES (?, ?)",
            (
                "websocket:all-records",
                json.dumps({
                    "artifacts": [{
                        "path": str(generated),
                        "metadata_path": str(metadata),
                    }],
                }),
            ),
        )
        connection.execute(
            "INSERT INTO pending_inputs VALUES (?, ?)",
            (
                "websocket:all-records",
                json.dumps({"content": f"![pending]({third})"}),
            ),
        )

    _write_session_db(
        sessions_root,
        "b" * 32,
        {"websocket:other-workspace": [{"role": "user", "media": [str(second)]}]},
    )
    result = scan_session_references(sessions_root, data_dir=tmp_path / "data")

    assert set(result.sessions) == {
        "websocket:all-records",
        "websocket:other-workspace",
    }
    for path in (first, generated, metadata, third, second):
        assert str(path) in result.paths
    assert str(
        tmp_path / "data" / "session-images" / image_id
    ) in result.paths
    assert "att_history" in result.managed_ids
    assert str(borrowed) in result.borrowed_paths


async def test_upload_counts_original_and_snapshot_and_staging_prune_releases_both(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import nanobot.channels.websocket.attachment_store as staging_module

    service = _service(tmp_path)
    staging = StagingAttachmentStore(
        tmp_path / "data" / "media" / "websocket",
        managed_store=service.store,
        ttl_seconds=10,
    )
    monkeypatch.setattr(staging_module.time, "monotonic", lambda: 100)

    async def chunks():
        yield PNG_BYTES

    reference = await staging.upload(
        chunks(),
        owner="owner",
        mime="image/png",
        size=len(PNG_BYTES),
    )
    staged_path = Path(staging.resolve([reference], owner="owner")[0])
    expected_snapshot_bytes = len(PNG_DATA_URL.encode("ascii"))
    assert staging.pending_bytes == len(PNG_BYTES)
    assert service.store.reserved_bytes == len(PNG_BYTES) + expected_snapshot_bytes
    assert service.store.list_active() == []

    monkeypatch.setattr(staging_module.time, "monotonic", lambda: 111)
    staging.prune()

    assert not staged_path.exists()
    assert service.store.reserved_bytes == 0
    assert service.store.list_active() == []


async def test_image_upload_capacity_is_reserved_before_reading_body(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path, max_bytes=len(PNG_BYTES))
    staging = StagingAttachmentStore(
        tmp_path / "data" / "media" / "websocket",
        managed_store=service.store,
    )

    async def unread():
        pytest.fail("capacity-rejected image body must not be consumed")
        yield b""

    with pytest.raises(AttachmentUploadError, match="storage limit"):
        await staging.upload(
            unread(),
            owner="owner",
            mime="image/png",
            size=len(PNG_BYTES),
        )
    assert service.store.reserved_bytes == 0


def test_registered_upload_and_snapshot_survive_restart_with_real_usage(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    path = _upload_path(service.data_dir)
    upload = service.register_upload(
        path,
        name="source.png",
        mime="image/png",
        session_key="websocket:restart",
    )
    snapshot = service.ensure_image_snapshot(
        path,
        None,
        session_key="websocket:restart",
        source_attachment_id=upload.id,
        reservation=service.reserve_image_snapshot("image/png", len(PNG_BYTES)),
    )
    assert snapshot.id == upload.id
    expected = len(PNG_BYTES) + len(PNG_DATA_URL.encode("ascii"))
    assert service.store.get(upload.id).bytes == expected
    assert service.usage().managed_bytes == expected

    _write_session_db(
        service.sessions_root,
        "c" * 32,
        {"websocket:restart": [{"role": "user", "media": [str(path)]}]},
    )
    restarted = _service(tmp_path)
    payload = restarted.management_payload()

    assert payload["settings"] == {
        "max_bytes": None,
        "auto_cleanup": False,
        "orphan_days": 30,
    }
    assert payload["usage"]["managed_bytes"] == expected
    assert payload["items"][0]["id"] == upload.id
    assert payload["items"][0]["references"] == [
        {"key": "websocket:restart", "title": "restart"},
    ]


def test_generated_image_and_metadata_are_registered_as_one_capacity_group(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(tmp_path, max_bytes=1)
    media_root = service.data_dir / "media"
    monkeypatch.setattr("nanobot.utils.artifacts.get_media_dir", lambda: media_root)

    with pytest.raises(AttachmentCapacityError):
        store_generated_image_artifact(
            PNG_DATA_URL,
            prompt="fixture",
            model="fixture-model",
            save_dir="generated",
            provider="fixture",
            session_key="websocket:generated",
            attachment_service=service,
        )
    if media_root.exists():
        assert not [
            path for path in media_root.rglob("*")
            if path.is_file()
        ]
    assert service.store.reserved_bytes == 0

    service.update_policy(max_bytes=None, auto_cleanup=False, orphan_days=30)
    artifact = store_generated_image_artifact(
        PNG_DATA_URL,
        prompt="fixture",
        model="fixture-model",
        save_dir="generated",
        provider="fixture",
        session_key="websocket:generated",
        attachment_service=service,
    )
    record = service.store.get(f"gen_{artifact['id']}")

    assert record.source == "generated"
    assert {file.role for file in record.files} == {"primary", "metadata"}
    assert service.usage().managed_bytes == record.bytes


def test_borrowed_and_unknown_files_inside_media_are_protected_from_cleanup(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path, auto_cleanup=True, orphan_days=1)
    borrowed = _upload_path(service.data_dir, "aaaaaaaaaaaa.png")
    unknown = service.data_dir / "media" / "websocket" / "manual-notes.bin"
    unknown.write_bytes(b"unknown")
    database = _write_session_db(
        service.sessions_root,
        "d" * 32,
        {
            "websocket:borrowed": [{
                "role": "user",
                "media": [str(borrowed)],
                "local_media_paths": [str(borrowed)],
            }],
        },
    )

    service.reconcile()
    payload = service.management_payload()

    assert payload["items"] == []
    assert payload["usage"]["protected_bytes"] == len(PNG_BYTES) + len(b"unknown")
    _delete_session_rows(database, {"websocket:borrowed"})

    restarted = _service(tmp_path, auto_cleanup=True, orphan_days=1)
    restarted.reconcile()
    restarted_payload = restarted.management_payload()
    assert restarted_payload["items"] == []
    assert restarted_payload["usage"]["protected_bytes"] == len(PNG_BYTES) + len(b"unknown")
    restarted.cleanup_orphans(now=datetime.now().astimezone() + timedelta(days=365))
    assert borrowed.exists()
    assert unknown.exists()


def test_temporary_path_exclusion_hides_usage_and_blocks_management_delete(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    path = _upload_path(service.data_dir)
    record = service.register_upload(
        path,
        name="temporary.png",
        mime="image/png",
        session_key="websocket:temporary",
    )
    service.retain_temporary_paths([path])

    payload = service.management_payload(reconcile=False)

    assert payload["items"] == []
    assert payload["usage"]["managed_bytes"] == 0
    assert payload["usage"]["protected_bytes"] == len(PNG_BYTES)
    preview = service.preview_delete([record.id])
    assert preview.items == ()
    assert preview.blocked == ({
        "id": record.id,
        "reason": "Attachment belongs to an active temporary chat",
    },)
    assert service.delete([record.id]).deleted == ()
    assert path.exists()

    service.release_temporary_paths([path])
    visible = service.management_payload(reconcile=False)
    assert [item["id"] for item in visible["items"]] == [record.id]
    assert visible["usage"]["managed_bytes"] == len(PNG_BYTES)


def test_same_session_key_across_namespaces_keeps_all_attachment_references(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path, auto_cleanup=True, orphan_days=1)
    first = _upload_path(service.data_dir, "111111111111.png")
    second = _upload_path(service.data_dir, "222222222222.png")
    first_db = _write_session_db(
        service.sessions_root,
        "a" * 32,
        {"websocket:shared": [{"role": "user", "media": [str(first)]}]},
    )
    _write_session_db(
        service.sessions_root,
        "b" * 32,
        {"websocket:shared": [{"role": "user", "media": [str(second)]}]},
    )

    scan = service.reconcile()

    assert scan.complete is True
    assert scan.sessions["websocket:shared"].paths == {
        str(first.resolve()),
        str(second.resolve()),
    }
    first_record = service.store.attachment_for_path(first)
    second_record = service.store.attachment_for_path(second)
    assert first_record is not None
    assert second_record is not None
    future = datetime.now().astimezone() + timedelta(days=365)
    assert service.cleanup_orphans(now=future).deleted == ()
    assert first.exists()
    assert second.exists()

    _delete_session_rows(first_db, {"websocket:shared"})
    service.reconcile()
    assert "websocket:shared" in {
        reference.key for reference in service.store.get(second_record.id).references
    }
    service.cleanup_orphans(now=future)
    assert second.exists()


@pytest.mark.parametrize("session_override", [False, True])
def test_relative_markdown_uses_namespace_or_session_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    session_override: bool,
) -> None:
    service = _service(tmp_path, auto_cleanup=True, orphan_days=1)
    workspace = tmp_path / "workspace"
    wrong_workspace = tmp_path / "wrong-workspace"
    cwd = tmp_path / "unrelated-cwd"
    for directory in (workspace, wrong_workspace, cwd):
        directory.mkdir()
    monkeypatch.chdir(cwd)
    source = workspace / "chart.png"
    source.write_bytes(PNG_BYTES)
    key = "cli:direct"
    database = _write_session_db(
        service.sessions_root,
        "a" * 32,
        {key: [{"role": "assistant", "content": "![chart](chart.png)"}]},
        metadata=(
            {key: {"workspace_scope": {"project_path": str(workspace)}}}
            if session_override
            else None
        ),
    )
    (database.parent / ".workspace").write_text(
        str(wrong_workspace if session_override else workspace),
        encoding="utf-8",
    )
    staged = service.data_dir / "media" / "websocket" / f"{'a' * 20}-chart.png"
    staged.parent.mkdir(parents=True, exist_ok=True)
    staged.write_bytes(PNG_BYTES)
    record = service.register_outbound_copy(
        staged,
        source_path=source,
        source_digest="a" * 20,
        session_key=key,
        reservation=service.reserve(len(PNG_BYTES), purpose="outbound-copy"),
    )
    service.store.replace_references({})

    scan = service.reconcile()

    assert scan.complete is True
    assert key in {reference.key for reference in service.store.get(record.id).references}
    restarted = _service(tmp_path, auto_cleanup=True, orphan_days=1)
    future = datetime.now().astimezone() + timedelta(days=365)
    assert restarted.cleanup_orphans(now=future).deleted == ()
    assert staged.exists()


def test_relative_markdown_without_workspace_marker_is_incomplete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.chdir(workspace)
    _write_session_db(
        service.sessions_root,
        "a" * 32,
        {"cli:direct": [{"role": "assistant", "content": "![chart](chart.png)"}]},
    )

    scan = service.reconcile()

    assert scan.complete is False
    assert scan.sessions["cli:direct"].paths == frozenset()


def test_chat_reclaim_handles_parent_then_fork_deletion_order(tmp_path: Path) -> None:
    service = _service(tmp_path)
    path = _upload_path(service.data_dir)
    record = service.register_upload(
        path,
        name="shared.png",
        mime="image/png",
        session_key="websocket:parent",
    )
    parent_db = _write_session_db(
        service.sessions_root,
        "e" * 32,
        {"websocket:parent": [{"role": "user", "media": [str(path)]}]},
    )
    fork_db = _write_session_db(
        service.sessions_root,
        "f" * 32,
        {"websocket:fork": [{"role": "user", "media": [str(path)]}]},
    )
    service.reconcile()
    assert {ref.key for ref in service.store.get(record.id).references} == {
        "websocket:parent",
        "websocket:fork",
    }

    _delete_session_rows(parent_db, {"websocket:parent"})
    service.reclaim_deleted_sessions({"websocket:parent"})
    assert path.exists()
    assert {ref.key for ref in service.store.get(record.id).references} == {
        "websocket:fork",
    }

    _delete_session_rows(fork_db, {"websocket:fork"})
    result = service.reclaim_deleted_sessions({"websocket:fork"})
    assert result.deleted == (record.id,)
    assert not path.exists()


def test_incomplete_namespace_scan_never_clears_refs_or_auto_deletes(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path, auto_cleanup=True, orphan_days=1)
    path = _upload_path(service.data_dir)
    record = service.register_upload(
        path,
        name="protected.png",
        mime="image/png",
        session_key="websocket:known",
    )
    _write_session_db(
        service.sessions_root,
        "3" * 32,
        {"websocket:known": [{"role": "user", "media": [str(path)]}]},
    )
    broken = service.sessions_root / ("4" * 32)
    broken.mkdir()
    (broken / "sessions.sqlite3").write_bytes(b"not a sqlite database")

    scan = service.reconcile()

    assert scan.complete is False
    assert record.id in {row.id for row in service.store.list_active()}
    assert [ref.key for ref in service.store.get(record.id).references] == [
        "websocket:known",
    ]
    future = datetime.now().astimezone() + timedelta(days=365)
    assert service.cleanup_orphans(now=future).deleted == ()
    assert path.exists()


def test_manual_delete_marks_history_without_recreating_or_replaying_image(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(tmp_path)
    path = _upload_path(service.data_dir)
    record = service.register_upload(
        path,
        name="manual.png",
        mime="image/png",
        session_key="websocket:manual",
    )
    service.ensure_image_snapshot(
        path,
        None,
        session_key="websocket:manual",
        source_attachment_id=record.id,
        reservation=service.reserve_image_snapshot("image/png", len(PNG_BYTES)),
    )
    snapshot = service.store.get(record.id).files[1]
    snapshot_path = service.store.resolve_relative(snapshot.relative_path)
    _write_session_db(
        service.sessions_root,
        "1" * 32,
        {
            "websocket:manual": [{
                "role": "user",
                "media": [str(path)],
                "content": [{
                    "type": "image_attachment",
                    "id": snapshot_path.name,
                    "managed_id": record.id,
                }],
            }],
        },
    )
    service.reconcile()

    preview = service.preview_delete([record.id])
    assert preview.affected_sessions[0].key == "websocket:manual"
    assert set(preview.items[0]["files"]) == {
        file.relative_path
        for file in service.store.get(record.id).files
    }
    expected_bytes = len(PNG_BYTES) + len(PNG_DATA_URL.encode("ascii"))
    assert preview.estimated_freed_bytes == expected_bytes

    result = service.delete([record.id])
    assert result.deleted == (record.id,)
    assert result.freed_bytes == expected_bytes
    assert not path.exists()
    assert not snapshot_path.exists()

    block = [{
        "type": "image_attachment",
        "id": snapshot_path.name,
        "managed_id": record.id,
    }]
    assert persist_user_image_blocks(
        block,
        session_key="websocket:manual",
        attachment_service=service,
    ) == block
    assert not snapshot_path.exists()

    service.reconcile()
    assert service.store.get(record.id).status == "deleted"
    assert not snapshot_path.exists()

    monkeypatch.setattr(
        "nanobot.session.image_attachments.existing_attachment_service",
        lambda: service,
    )
    replayed = replay_user_image_blocks(block)
    assert replayed == [{"type": "text", "text": IMAGE_UNAVAILABLE}]

    gateway = WebUIMediaGateway(
        workspace_path=tmp_path,
        logger=Mock(),
        media_dir=lambda _channel: service.data_dir / "media",
        attachments=service,
    )
    projected = gateway.augment_transcript_user_media(
        [str(path)],
        [],
        session_key="websocket:manual",
    )
    assert projected[0]["managed_id"] == record.id
    assert projected[0]["deleted"] is True
    assert projected[0]["url"] == ""

    replacement = _upload_path(service.data_dir, "fedcba987654.png")
    new_record = service.register_upload(
        replacement,
        name="manual.png",
        mime="image/png",
        session_key="websocket:manual",
    )
    service.ensure_image_snapshot(
        replacement,
        None,
        session_key="websocket:manual",
        source_attachment_id=new_record.id,
        reservation=service.reserve_image_snapshot("image/png", len(PNG_BYTES)),
    )
    assert new_record.id != record.id
    assert replacement.exists()
    assert snapshot_path.exists()
    refreshed = gateway.augment_transcript_user_media(
        [str(replacement)],
        [],
        session_key="websocket:manual",
    )
    assert refreshed[0]["managed_id"] == new_record.id
    assert refreshed[0].get("deleted") is not True


def test_automatic_cleanup_is_opt_in_and_starts_when_last_reference_disappears(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    path = _upload_path(service.data_dir)
    record = service.register_upload(
        path,
        name="cleanup.png",
        mime="image/png",
        session_key="websocket:cleanup",
    )
    database = _write_session_db(
        service.sessions_root,
        "2" * 32,
        {"websocket:cleanup": [{"role": "user", "media": [str(path)]}]},
    )
    service.reconcile()
    assert service.store.get(record.id).unreferenced_since is None
    assert service.cleanup_orphans(
        now=datetime.now().astimezone() + timedelta(days=365),
    ).deleted == ()
    assert path.exists()

    service.update_policy(max_bytes=None, auto_cleanup=True, orphan_days=30)
    _delete_session_rows(database, {"websocket:cleanup"})
    service.reconcile()
    start = datetime.fromisoformat(service.store.get(record.id).unreferenced_since or "")

    restarted = _service(tmp_path, auto_cleanup=True, orphan_days=30)
    assert restarted.cleanup_orphans(
        now=start + timedelta(days=29),
    ).deleted == ()
    assert path.exists()

    result = restarted.cleanup_orphans(now=start + timedelta(days=31))
    assert result.deleted == (record.id,)
    assert not path.exists()


def test_outbound_copy_is_reused_then_deleted_without_staging_again(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    source = tmp_path / "workspace" / "report.pdf"
    source.parent.mkdir()
    source.write_bytes(b"first version")
    media_root = service.data_dir / "media"

    def media_dir(channel: str | None) -> Path:
        return media_root if channel is None else media_root / channel

    first = sign_or_stage_media_path(
        source,
        secret=b"secret",
        media_dir=media_dir,
        session_key="websocket:outbound",
        attachments=service,
    )
    assert first is not None
    records = [
        record for record in service.store.list_active()
        if record.source == "outbound_copy"
    ]
    assert len(records) == 1
    staged = service.store.resolve_relative(records[0].files[0].relative_path)
    assert staged.read_bytes() == b"first version"

    second = sign_or_stage_media_path(
        source,
        secret=b"secret",
        media_dir=media_dir,
        session_key="websocket:outbound",
        attachments=service,
    )
    assert second == first
    assert len([
        record for record in service.store.list_active()
        if record.source == "outbound_copy"
    ]) == 1

    service.delete([records[0].id])
    assert not staged.exists()
    assert sign_or_stage_media_path(
        source,
        secret=b"secret",
        media_dir=media_dir,
        session_key="websocket:outbound",
        attachments=service,
    ) is None
    assert not staged.exists()

    source.write_bytes(b"second version")
    third = sign_or_stage_media_path(
        source,
        secret=b"secret",
        media_dir=media_dir,
        session_key="websocket:outbound",
        attachments=service,
    )
    assert third is not None
    active = [
        record for record in service.store.list_active()
        if record.source == "outbound_copy"
    ]
    assert len(active) == 1
    assert active[0].id != records[0].id
    assert service.store.resolve_relative(
        active[0].files[0].relative_path,
    ).read_bytes() == b"second version"
