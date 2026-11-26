from __future__ import annotations

import secrets
from collections.abc import Callable
from pathlib import Path
from typing import Any

from websockets.http11 import Request as WsRequest
from websockets.http11 import Response

from nanobot.attachments.service import AttachmentService
from nanobot.attachments.store import AttachmentRecord
from nanobot.config.paths import get_media_dir
from nanobot.security.workspace_access import WorkspaceScope
from nanobot.webui.media_api import (
    media_attachment_kind,
    media_path_from_history_signed_url,
    serve_signed_media,
    sign_local_attachment,
    sign_media_path,
    sign_or_stage_media_path,
)
from nanobot.webui.transcript import (
    AssistantTextProjection,
    project_history_markdown_media,
    rewrite_local_markdown_images,
)


def _default_media_dir(channel: str | None) -> Path:
    return get_media_dir(channel)


class WebUIMediaGateway:
    """Own media URL signing and WebUI markdown/media augmentation."""

    def __init__(
        self,
        *,
        workspace_path: Path,
        logger: Any,
        media_dir: Callable[[str | None], Path] | None = None,
        secret: bytes | None = None,
        attachments: AttachmentService | None = None,
    ) -> None:
        self.workspace_path = workspace_path
        self.logger = logger
        self._media_dir: Callable[[str | None], Path] = media_dir or _default_media_dir
        self.secret = secret or secrets.token_bytes(32)
        self.attachments = attachments

    def serve_signed_media(
        self,
        sig: str,
        payload: str,
        *,
        request: WsRequest | None = None,
        scope_for_session: Callable[[str], WorkspaceScope | None] | None = None,
    ) -> Response:
        return serve_signed_media(
            sig,
            payload,
            secret=self.secret,
            request=request,
            media_dir=self._media_dir,
            scope_for_session=scope_for_session,
        )

    def sign_or_stage_media_path(
        self,
        path: Path,
        *,
        session_key: str | None = None,
        temporary_namespace: str | None = None,
        register_temporary_copy: Callable[[Path], bool] | None = None,
    ) -> dict[str, str] | None:
        return sign_or_stage_media_path(
            path,
            secret=self.secret,
            media_dir=self._media_dir,
            logger=self.logger,
            session_key=session_key,
            attachments=self.attachments,
            temporary_namespace=temporary_namespace,
            register_temporary_copy=register_temporary_copy,
        )

    def rewrite_local_markdown_images(
        self,
        text: str,
        *,
        workspace_path: Path | None = None,
        session_key: str | None = None,
        temporary_namespace: str | None = None,
        register_temporary_copy: Callable[[Path], bool] | None = None,
    ) -> str:
        return rewrite_local_markdown_images(
            text,
            workspace_path=workspace_path or self.workspace_path,
            sign_path=lambda path: self.sign_or_stage_media_path(
                path,
                session_key=session_key,
                temporary_namespace=temporary_namespace,
                register_temporary_copy=register_temporary_copy,
            ),
        )

    def augment_transcript_assistant_text(
        self,
        text: str,
        *,
        workspace_path: Path | None = None,
        session_key: str | None = None,
    ) -> AssistantTextProjection:
        def resolve_reference(reference: str) -> tuple[str | None, dict[str, Any] | None]:
            signed = reference.startswith("/api/media/")
            if signed:
                path = media_path_from_history_signed_url(
                    reference, media_dir=self._media_dir,
                )
                if path is None:
                    return None, None
                managed = self._history_managed_record(path, session_key)
                if managed is None:
                    return reference, None
            else:
                path = Path(reference)
                managed = self._managed_record(
                    path, session_key, include_outbound=True,
                )
            if managed is not None and managed.status == "deleted":
                return None, {
                    "kind": managed.kind,
                    "name": managed.name or path.name,
                    "managed_id": managed.id,
                    "deleted": True,
                    "url": "",
                }
            if signed:
                projected = self._sign_managed_record(managed)
                return (
                    str(projected["url"])
                    if projected is not None and projected.get("url")
                    else None
                ), None
            projected = self.sign_or_stage_media_path(
                path, session_key=session_key,
            )
            return (
                str(projected["url"])
                if projected is not None and projected.get("url")
                else None
            ), None

        return project_history_markdown_media(
            text,
            workspace_path=workspace_path or self.workspace_path,
            resolve_reference=resolve_reference,
        )

    def augment_transcript_media(
        self, paths: list[str], *, session_key: str | None = None,
    ) -> list[dict[str, Any]]:
        media: list[dict[str, Any]] = []
        for raw in paths:
            path = Path(raw)
            managed = self._managed_record(path, session_key, include_outbound=True)
            entry: dict[str, Any] = {
                "kind": media_attachment_kind(path.name),
                "name": path.name,
                "url": "",
            }
            if managed is not None:
                entry["managed_id"] = managed.id
                if managed.status == "deleted":
                    entry["deleted"] = True
                    media.append(entry)
                    continue
                if managed.status == "missing":
                    media.append(entry)
                    continue
            signed = (
                self.sign_or_stage_media_path(path, session_key=session_key)
                if path.is_file()
                else None
            )
            if signed is None and managed is not None:
                signed = self._sign_managed_record(managed)
            if signed is not None:
                entry.update(signed)
            media.append(entry)
        return media

    def local_attachment_url(self, path: str, *, session_key: str) -> str:
        return sign_local_attachment(path, session_key=session_key, secret=self.secret)

    def augment_transcript_user_media(
        self, paths: list[str], local_paths: list[str], *, session_key: str,
    ) -> list[dict[str, Any]]:
        local = set(local_paths)
        media: list[dict[str, Any]] = []
        for raw in paths:
            path = Path(raw)
            managed = self._managed_record(
                path, session_key, include_source_snapshot=True,
            )
            try:
                if managed is not None and managed.status == "deleted":
                    url = ""
                elif managed is not None and managed.status == "missing":
                    url = ""
                elif not path.is_file():
                    url = ""
                elif raw in local:
                    url = self.local_attachment_url(raw, session_key=session_key)
                else:
                    url = sign_media_path(path, secret=self.secret, media_dir=self._media_dir) or ""
            except OSError:
                url = ""
            entry: dict[str, Any] = {
                "kind": media_attachment_kind(path.name), "name": path.name, "url": url,
            }
            if managed is not None:
                entry["managed_id"] = managed.id
                if managed.status == "deleted":
                    entry["deleted"] = True
            media.append(entry)
        return media

    def _managed_record(
        self,
        path: Path,
        session_key: str | None,
        *,
        include_outbound: bool = False,
        include_source_snapshot: bool = False,
    ) -> AttachmentRecord | None:
        if self.attachments is None:
            return None
        try:
            records = self.attachments.store.records_for_path(path)
        except (OSError, ValueError):
            records = []
        if session_key is not None:
            owned = [
                record for record in records
                if record.owner_session_key == session_key
            ]
            if owned:
                return max(
                    owned,
                    key=lambda record: (
                        record.status == "available",
                        record.status != "deleted",
                        record.created_at,
                    ),
                )
        if records:
            return max(
                records,
                key=lambda record: (
                    record.status == "available",
                    record.status != "deleted",
                    record.created_at,
                ),
            )
        if include_outbound and session_key is not None:
            return self.attachments.find_outbound_for_source_path(
                path, session_key, include_deleted=True,
            )
        if include_source_snapshot and session_key is not None:
            return self.attachments.find_image_snapshot_for_source_path(
                path, session_key, include_deleted=True,
            )
        return None

    def _history_managed_record(
        self,
        path: Path,
        session_key: str | None,
    ) -> AttachmentRecord | None:
        if self.attachments is None or session_key is None:
            return None
        try:
            records = self.attachments.store.records_for_path(path)
        except (OSError, ValueError):
            return None
        associated = [
            record for record in records
            if record.owner_session_key == session_key
            or any(reference.key == session_key for reference in record.references)
        ]
        if not associated:
            return None
        return max(
            associated,
            key=lambda record: (
                record.status == "available",
                record.status != "deleted",
                record.created_at,
            ),
        )

    def _sign_managed_record(self, record: AttachmentRecord) -> dict[str, str] | None:
        if self.attachments is None:
            return None
        for file in record.files:
            try:
                path = self.attachments.store.resolve_relative(file.relative_path)
            except ValueError:
                continue
            if not path.is_file():
                continue
            signed = sign_media_path(path, secret=self.secret, media_dir=self._media_dir)
            if signed is not None:
                return {"url": signed, "name": record.name}
        return None
