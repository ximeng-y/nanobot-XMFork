"""Durable user image blocks and their model-facing replay projection."""

from __future__ import annotations

import re
from collections.abc import Sequence
from hashlib import sha256
from typing import TYPE_CHECKING, TypedDict, cast

from nanobot.attachments.runtime import existing_attachment_service
from nanobot.config.paths import get_data_dir
from nanobot.providers.image_input import REPLAYED_IMAGE_META
from nanobot.session.message_content import IMAGE_ATTACHMENT_TYPE
from nanobot.utils.atomic_file import atomic_write_text, io_path, read_atomic_bytes

if TYPE_CHECKING:
    from nanobot.attachments.service import AttachmentService

_IMAGE_ID = re.compile(r"[0-9a-f]{64}")
IMAGE_UNAVAILABLE = "[Previous image unavailable]"


class ImageAttachment(TypedDict):
    type: str
    id: str


class ManagedImageAttachment(ImageAttachment, total=False):
    managed_id: str


def persist_user_image_blocks(
    content: Sequence[object],
    *,
    session_key: str | None = None,
    attachment_service: AttachmentService | None = None,
) -> list[object]:
    """Replace inline images with attachment references for session replay."""
    stored: list[object] = []
    for value in content:
        block = cast(dict[str, object], value) if isinstance(value, dict) else {}
        if block.get("type") == IMAGE_ATTACHMENT_TYPE:
            ref: ManagedImageAttachment = {
                "type": IMAGE_ATTACHMENT_TYPE,
                "id": cast(str, block.get("id", "")),
            }
            managed_id = block.get("managed_id")
            if isinstance(managed_id, str):
                ref["managed_id"] = managed_id
            stored.append(ref)
            continue
        image = block.get("image_url")
        url = cast(dict[str, object], image).get("url") if isinstance(image, dict) else None
        if block.get("type") != "image_url" or not isinstance(url, str) or not url.startswith("data:image/"):
            stored.append(cast(object, value))
            continue
        metadata = block.get("_meta")
        if isinstance(metadata, dict) and REPLAYED_IMAGE_META in cast(dict[str, object], metadata):
            stored.append(cast(object, value))
            continue
        image_id = sha256(url.encode("ascii")).hexdigest()
        ref: ManagedImageAttachment = {"type": IMAGE_ATTACHMENT_TYPE, "id": image_id}
        if attachment_service is not None and session_key:
            snapshot = attachment_service.ensure_image_snapshot(
                None, url, session_key=session_key,
            )
            ref["managed_id"] = snapshot.id
        else:
            directory = io_path(get_data_dir() / "session-images")
            path = directory / image_id
            if not path.is_file():
                directory.mkdir(mode=0o700, exist_ok=True)
                atomic_write_text(path, url)
        stored.append(ref)
    return stored


def replay_user_image_blocks(content: Sequence[object]) -> list[object]:
    """Materialize the selected user blocks for a model request."""
    service = existing_attachment_service()
    replay: list[object] = []
    for value in content:
        block = cast(dict[str, object], value) if isinstance(value, dict) else {}
        if block.get("type") == IMAGE_ATTACHMENT_TYPE:
            managed_id = block.get("managed_id")
            if isinstance(managed_id, str) and service is not None:
                try:
                    if service.store.get(managed_id).status != "available":
                        replay.append({"type": "text", "text": IMAGE_UNAVAILABLE})
                        continue
                except KeyError:
                    pass
            url = _load_image(block.get("id"))
            if url is not None:
                replay.append({
                    "type": "image_url", "image_url": {"url": url},
                    "_meta": {REPLAYED_IMAGE_META: True},
                })
            else:
                replay.append({"type": "text", "text": IMAGE_UNAVAILABLE})
        elif block.get("type") == "image_url":
            # Temporary sessions keep their images in memory only.
            replay.append({**block, "_meta": {REPLAYED_IMAGE_META: True}})
        else:
            replay.append(cast(object, value))
    return replay


def _load_image(image_id: object) -> str | None:
    if not isinstance(image_id, str) or not _IMAGE_ID.fullmatch(image_id):
        return None
    try:
        path = io_path(get_data_dir() / "session-images" / image_id)
        if not path.is_file():
            return None
        data = read_atomic_bytes(path)
        if data.startswith(b"data:image/") and sha256(data).hexdigest() == image_id:
            return data.decode("ascii")
    except (OSError, UnicodeError):
        pass
    return None
