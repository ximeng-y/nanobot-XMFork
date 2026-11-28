from __future__ import annotations

import json
import re
from typing import Any, cast

import httpx
from packaging.version import InvalidVersion, Version

import nanobot
from nanobot.repository import TAGS_URL, release_url
from nanobot.security.network import PinnedDNSAsyncTransport
from nanobot.update_channel import read_update_channel

_MAX_TAGS_RESPONSE = 256 * 1024
_FORK_TAG = re.compile(r"^v(?P<public>\d+\.\d+\.\d+(?:\.(?:dev|post)\d+)?)-xm\.(?P<build>\d+)$")


def _release_version(tag: str) -> str | None:
    """Map the Fork's Git tag spelling to its PEP 440 package version."""
    match = _FORK_TAG.fullmatch(tag)
    if match is None:
        return None
    value = f"{match.group('public')}+xm.{match.group('build')}"
    try:
        return str(Version(value))
    except InvalidVersion:
        return None


async def _github_tags() -> object:
    async with httpx.AsyncClient(
        transport=PinnedDNSAsyncTransport(),
        trust_env=False,
        timeout=20,
        follow_redirects=False,
        headers={
            "User-Agent": "nanobot-release-check",
            "Accept": "application/vnd.github+json",
            "Accept-Encoding": "identity",
        },
    ) as client:
        async with client.stream("GET", TAGS_URL) as response:
            if response.status_code != 200:
                raise ValueError("GitHub release tags are unavailable")
            payload = bytearray()
            async for chunk in response.aiter_bytes():
                payload.extend(chunk)
                if len(payload) > _MAX_TAGS_RESPONSE:
                    raise ValueError("GitHub release tags exceed the size limit")
    return json.loads(payload)


def _github_tag_names(payload: object) -> list[str]:
    """Validate GitHub's tag list before its names reach version parsing."""
    if not isinstance(payload, list):
        raise ValueError("GitHub returned an invalid release tag list")
    names: list[str] = []
    for item in cast(list[object], payload):
        if not isinstance(item, dict):
            continue
        name = cast(dict[str, object], item).get("name")
        if isinstance(name, str):
            names.append(name)
    return names


async def _latest_fork_tag(channel: str) -> tuple[Version, str, str] | None:
    tags = _github_tag_names(await _github_tags())
    candidates: list[tuple[Version, str, str]] = []
    for tag in tags:
        version = _release_version(tag)
        if version is None:
            continue
        parsed = Version(version)
        if channel == "stable" and parsed.is_prerelease:
            continue
        candidates.append((parsed, version, tag))
    return max(candidates, key=lambda item: item[0], default=None)


async def check_for_update() -> dict[str, Any]:
    """Check this Fork's latest GitHub tag without installing anything."""
    selection = read_update_channel()
    current = nanobot.__version__
    status: dict[str, Any] = {
        "channel": selection.channel, "revision": selection.revision,
        "currentVersion": current, "state": "unavailable",
    }
    result: dict[str, Any] = {"updateAvailable": None, "releaseStatus": status}
    try:
        latest = await _latest_fork_tag(selection.channel)
        if latest is not None and latest[0] > Version(current):
            result["updateAvailable"] = {
                "currentVersion": current, "latestVersion": latest[1],
                "releaseUrl": release_url(latest[2]),
            }
            status["state"] = "available"
        elif latest is not None:
            status["state"] = (
                "waiting_stable" if selection.channel == "stable" and Version(current).is_prerelease
                else "ahead" if Version(current) > latest[0] else "current"
            )
    except (httpx.HTTPError, ValueError, OSError):
        status["state"] = "unavailable"
    if read_update_channel() != selection:
        raise ValueError("Update channel changed while checking; check again")
    return result
