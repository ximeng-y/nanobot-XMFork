from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from nanobot.attachments.store import (
    AttachmentCapacityError,
    AttachmentFileSpec,
    AttachmentPathError,
    AttachmentReference,
    AttachmentStore,
)


def _managed_file(data_dir: Path, name: str = "0123456789ab.bin", size: int = 7) -> Path:
    path = data_dir / "media" / "websocket" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    return path


def test_capacity_reservations_are_concurrent_and_release_cleanly(tmp_path: Path) -> None:
    store = AttachmentStore(tmp_path, max_bytes=10)

    def reserve(index: int):
        try:
            return store.reserve(4, purpose=f"worker-{index}")
        except AttachmentCapacityError:
            return None

    with ThreadPoolExecutor(max_workers=8) as executor:
        reservations = list(executor.map(reserve, range(8)))

    acquired = [reservation for reservation in reservations if reservation is not None]
    assert len(acquired) == 2
    assert store.reserved_bytes == 8
    for reservation in acquired:
        reservation.release()
    assert store.reserved_bytes == 0
    with pytest.raises(AttachmentCapacityError):
        store.reserve(11)

    store.set_max_bytes(1)
    assert store.max_bytes == 1


def test_shared_file_is_counted_once_and_deleted_only_with_its_last_owner(
    tmp_path: Path,
) -> None:
    store = AttachmentStore(tmp_path)
    path = _managed_file(tmp_path)
    first = store.register(
        name="first.bin",
        kind="file",
        source="upload",
        files=[AttachmentFileSpec(path)],
        references=[AttachmentReference("websocket:first", "First")],
    )
    second = store.register(
        name="second.bin",
        kind="file",
        source="upload",
        files=[AttachmentFileSpec(path)],
        references=[AttachmentReference("websocket:second", "Second")],
    )

    assert store.usage().managed_bytes == 7
    first_preview = store.preview_delete([first.id])
    assert first_preview.estimated_freed_bytes == 0
    assert first_preview.affected_sessions[0].key == "websocket:first"

    first_result = store.delete([first.id])
    assert first_result.deleted == (first.id,)
    assert first_result.freed_bytes == 0
    assert path.exists()
    assert store.get(first.id).status == "deleted"

    second_preview = store.preview_delete([second.id])
    assert second_preview.estimated_freed_bytes == 7
    second_result = store.delete([second.id])
    assert second_result.deleted == (second.id,)
    assert second_result.freed_bytes == 7
    assert not path.exists()


def test_delete_failure_keeps_record_and_does_not_report_freed_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = AttachmentStore(tmp_path)
    path = _managed_file(tmp_path)
    record = store.register(
        name="kept.bin",
        kind="file",
        source="upload",
        files=[AttachmentFileSpec(path)],
        references=[AttachmentReference("websocket:kept", "Kept")],
    )
    original_unlink = Path.unlink

    def fail_target(self: Path, *args: object, **kwargs: object) -> None:
        if self == path:
            raise PermissionError("fixture denied deletion")
        original_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_target)
    result = store.delete([record.id])

    assert result.deleted == ()
    assert result.freed_bytes == 0
    assert result.failed[0]["id"] == record.id
    assert "fixture denied deletion" in result.failed[0]["reason"]
    assert path.exists()
    assert store.get(record.id).status == "available"


def test_registration_rejects_paths_outside_managed_roots_and_symlinks(
    tmp_path: Path,
    create_symlink,
) -> None:
    store = AttachmentStore(tmp_path / "data")
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"outside")
    with pytest.raises(AttachmentPathError):
        store.register(
            name="outside.bin",
            kind="file",
            source="upload",
            files=[AttachmentFileSpec(outside)],
        )

    target = _managed_file(tmp_path / "data", "fedcba987654.bin")
    link = tmp_path / "data" / "media" / "websocket" / "abcdef123456.bin"
    create_symlink(link, target)
    with pytest.raises(AttachmentPathError):
        store.register(
            name="linked.bin",
            kind="file",
            source="upload",
            files=[AttachmentFileSpec(link)],
        )


def test_cleanup_uses_persisted_unreferenced_since_and_respects_references(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    store = AttachmentStore(data_dir)
    path = _managed_file(data_dir)
    record = store.register(
        name="orphan.bin",
        kind="file",
        source="upload",
        files=[AttachmentFileSpec(path)],
        initially_referenced=False,
    )
    assert record.unreferenced_since is not None
    start = datetime.fromisoformat(record.unreferenced_since)
    with sqlite3.connect(store.db_path) as connection:
        connection.execute(
            "UPDATE managed_attachments SET created_at=? WHERE id=?",
            ("2000-01-01T00:00:00+00:00", record.id),
        )

    restarted = AttachmentStore(data_dir)
    assert restarted.cleanup_orphans(
        orphan_days=30,
        now=start + timedelta(days=29),
    ).deleted == ()
    assert path.exists()

    restarted.replace_references({
        record.id: [AttachmentReference("websocket:kept", "Kept")],
    })
    assert restarted.get(record.id).unreferenced_since is None
    assert restarted.cleanup_orphans(
        orphan_days=30,
        now=start + timedelta(days=31),
    ).deleted == ()
    assert path.exists()

    restarted.replace_references({})
    restarted.delete([record.id])
    assert not path.exists()
