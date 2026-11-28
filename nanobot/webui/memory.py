"""Read-only memory browsing and imported-source management for WebUI."""

from __future__ import annotations

import os
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from nanobot.agent.memory_imports import MemoryImportError, MemoryImportStore, is_path_link

_RESIDENT_PATHS = (
    "SOUL.md",
    "USER.md",
    "memory/MEMORY.md",
)


class MemoryAPIError(ValueError):
    """A WebUI memory request failure with an HTTP-compatible status."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


class WebUIMemoryService:
    """Serve only the agent's documented memory surface."""

    def __init__(self, workspace: Path):
        self.workspace = Path(workspace).expanduser().resolve()
        self.imports = MemoryImportStore(self.workspace)

    @property
    def max_import_bytes(self) -> int:
        return self.imports.MAX_IMPORT_BYTES

    @staticmethod
    def _parts(path: object) -> list[str] | None:
        if not isinstance(path, str) or not path or len(path) > 1024:
            return None
        if (
            path.startswith("/")
            or "\\" in path
            or ":" in path
            or "\x00" in path
            or any(ord(char) < 32 for char in path)
        ):
            return None
        parts = path.split("/")
        if any(
            not part
            or part in {".", ".."}
            or part.endswith((" ", "."))
            for part in parts
        ):
            return None
        return parts

    def _path_has_links(self, root: Path, parts: Sequence[str]) -> bool:
        current = root
        for part in parts:
            current = current / part
            if is_path_link(current):
                return True
        return False

    def _document_path(self, path: object) -> Path:
        parts = self._parts(path)
        if parts is None:
            raise MemoryAPIError(400, "Invalid memory document path")
        normalized = "/".join(parts)
        if normalized in _RESIDENT_PATHS:
            root = self.workspace
            relative = parts
        elif (
            len(parts) >= 3
            and parts[0] == "memory"
            and parts[1] == "wiki"
            and parts[-1].lower().endswith(".md")
        ):
            root = self.workspace / "memory" / "wiki"
            relative = parts[2:]
            if self._path_has_links(self.workspace, parts[:2]):
                raise MemoryAPIError(404, "Memory document not found")
        else:
            raise MemoryAPIError(404, "Memory document not found")

        if is_path_link(root) or self._path_has_links(root, relative):
            raise MemoryAPIError(404, "Memory document not found")
        candidate = root.joinpath(*relative)
        try:
            resolved_root = root.resolve(strict=True)
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(resolved_root)
        except (OSError, ValueError) as exc:
            raise MemoryAPIError(404, "Memory document not found") from exc
        if not resolved.is_file():
            raise MemoryAPIError(404, "Memory document not found")
        return resolved

    def _documents(self) -> list[dict[str, str]]:
        paths: list[str] = []
        for relative in _RESIDENT_PATHS:
            parts = relative.split("/")
            if self._path_has_links(self.workspace, parts):
                continue
            path = self.workspace.joinpath(*parts)
            if path.is_file():
                paths.append(relative)

        wiki_root = self.workspace / "memory" / "wiki"
        if (
            wiki_root.is_dir()
            and not self._path_has_links(self.workspace, ["memory", "wiki"])
        ):
            for directory, dirnames, filenames in os.walk(wiki_root, followlinks=False):
                base = Path(directory)
                dirnames[:] = [
                    name
                    for name in dirnames
                    if not is_path_link(base / name)
                    and not name.endswith((" ", "."))
                    and ":" not in name
                ]
                for filename in filenames:
                    if not filename.lower().endswith(".md"):
                        continue
                    path = base / filename
                    if is_path_link(path) or not path.is_file():
                        continue
                    relative = path.relative_to(self.workspace).as_posix()
                    if self._parts(relative) is not None:
                        paths.append(relative)
        return [{"path": path} for path in sorted(set(paths))]

    def payload(self) -> dict[str, Any]:
        try:
            documents = self._documents()
            imports = self.imports.list_entries()
        except OSError as exc:
            raise MemoryAPIError(500, "Memory files could not be read") from exc
        return {
            "documents": documents,
            "imports": imports,
            "max_import_bytes": self.max_import_bytes,
        }

    def read_document(self, path: object) -> dict[str, str]:
        document = self._document_path(path)
        try:
            content = document.read_bytes().decode("utf-8-sig")
        except (OSError, UnicodeDecodeError) as exc:
            raise MemoryAPIError(404, "Memory document not found") from exc
        return {"path": "/".join(self._parts(path) or ()), "content": content}

    def read_import(self, import_id: object) -> dict[str, Any]:
        try:
            entry, content = self.imports.read_import(import_id)
        except MemoryImportError as exc:
            raise MemoryAPIError(exc.status, exc.message) from exc
        return {"entry": entry, "content": content}

    def import_file(self, name: object, content_base64: object) -> dict[str, Any]:
        try:
            return self.imports.import_bytes(name, content_base64)
        except MemoryImportError as exc:
            raise MemoryAPIError(exc.status, exc.message) from exc

    def delete_archived(self, ids: object) -> dict[str, list[str]]:
        try:
            return self.imports.delete_archived(ids)
        except MemoryImportError as exc:
            raise MemoryAPIError(exc.status, exc.message) from exc
