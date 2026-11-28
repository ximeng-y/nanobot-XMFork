"""Canonical GitHub repository for this personal fork."""

import re
from dataclasses import dataclass

from packaging.version import Version

OWNER = "ximeng-y"
NAME = "nanobot-XMFork"
HTML_URL = f"https://github.com/{OWNER}/{NAME}"
SOURCE_URL = f"{HTML_URL}.git"
TAGS_URL = f"https://api.github.com/repos/{OWNER}/{NAME}/tags?per_page=100"


def release_url(tag: str) -> str:
    return f"{HTML_URL}/releases/tag/{tag}"


def fork_version(tag: str) -> str | None:
    match = re.fullmatch(r"v(\d+\.\d+\.\d+(?:\.(?:dev|post)\d+)?)-xm\.(\d+)", tag, re.ASCII)
    return str(Version(f"{match[1]}+xm.{match[2]}")) if match else None


@dataclass(frozen=True)
class ForkTarget:
    tag: str
    commit: str

    def __post_init__(self) -> None:
        if not fork_version(self.tag) or not re.fullmatch(r"[0-9a-f]{40}", self.commit):
            raise ValueError("Invalid Fork update target")

    @property
    def version(self) -> str:
        version = fork_version(self.tag)
        assert version is not None
        return version
