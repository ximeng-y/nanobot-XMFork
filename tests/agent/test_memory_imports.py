import base64

import pytest

from nanobot.agent.memory_imports import MAX_IMPORT_BYTES, MemoryImportError, MemoryImportStore


def encoded(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def test_import_preserves_raw_bytes_and_same_name_does_not_overwrite(tmp_path):
    store = MemoryImportStore(tmp_path)
    raw = "\ufeff# 第一版\r\n保留原始字节  \r\n".encode("utf-8")

    first = store.import_bytes("notes.md", encoded(raw))
    second = store.import_bytes("notes.md", encoded(b"# Second\n"))

    assert first["id"] != second["id"]
    assert first["status"] == second["status"] == "pending"
    assert first["bytes"] == len(raw)
    assert (store.pending_dir / first["id"] / "content.md").read_bytes() == raw
    preview, content = store.read_import(first["id"])
    assert preview["name"] == "notes.md"
    assert content == raw.decode("utf-8-sig")
    assert {entry["id"] for entry in store.list_entries()} == {first["id"], second["id"]}


@pytest.mark.parametrize(
    ("name", "payload", "status"),
    [
        ("../notes.md", encoded(b"# Note\n"), 400),
        ("notes.txt", encoded(b"text"), 400),
        ("notes.md", "not base64!", 400),
        ("notes.md", encoded(b""), 400),
        ("notes.md", encoded(b"text\0more"), 400),
        ("notes.md", encoded(b"\xff\xfe"), 400),
    ],
)
def test_import_rejects_invalid_files(tmp_path, name, payload, status):
    store = MemoryImportStore(tmp_path)

    with pytest.raises(MemoryImportError) as error:
        store.import_bytes(name, payload)

    assert error.value.status == status
    assert store.list_entries() == []


def test_import_rejects_oversize_file(tmp_path):
    store = MemoryImportStore(tmp_path)

    with pytest.raises(MemoryImportError) as error:
        store.import_bytes("notes.md", encoded(b"x" * (MAX_IMPORT_BYTES + 1)))

    assert error.value.status == 413
    assert store.list_entries() == []


def test_import_rejects_symlinked_memory_root(tmp_path, create_symlink):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    create_symlink(workspace / "memory", outside, target_is_directory=True)

    with pytest.raises(MemoryImportError) as error:
        MemoryImportStore(workspace)

    assert error.value.status == 500
    assert list(outside.iterdir()) == []


def test_archive_delete_only_removes_archived_entries(tmp_path):
    store = MemoryImportStore(tmp_path)
    archived = store.import_bytes("archived.md", encoded(b"# Archived\n"))
    pending = store.import_bytes("pending.md", encoded(b"# Pending\n"))

    archived_entry = store.archive(archived["id"])
    result = store.delete_archived([archived["id"], pending["id"], "not-an-id"])

    assert archived_entry["status"] == "archived"
    assert archived_entry["archived_at"].endswith("Z")
    assert result == {"deleted": [archived["id"]], "failed": [pending["id"], "not-an-id"]}
    assert store.read_import(pending["id"])[0]["status"] == "pending"
    with pytest.raises(MemoryImportError) as error:
        store.read_import(archived["id"])
    assert error.value.status == 404


def test_failed_import_remains_retryable_and_readable(tmp_path):
    store = MemoryImportStore(tmp_path)
    imported = store.import_bytes("failed.md", encoded(b"# Retry me\n"))

    store.mark_failed(imported["id"], "Dream did not confirm")

    entry = store.list_entries()[0]
    assert entry["status"] == "failed"
    assert entry["error"] == "Dream did not confirm"
    assert store.retryable_ids() == [imported["id"]]
    assert store.read_import(imported["id"])[1] == "# Retry me\n"
