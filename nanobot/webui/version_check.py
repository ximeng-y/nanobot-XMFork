from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any, cast

import httpx
from packaging.version import Version

import nanobot
from nanobot.repository import TAGS_URL, ForkTarget, fork_version, release_url
from nanobot.security.network import PinnedDNSAsyncTransport
from nanobot.update_channel import read_update_channel

_MAX_TAGS_RESPONSE = 256 * 1024


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


def _github_targets(payload: object) -> list[ForkTarget]:
    if not isinstance(payload, list):
        raise ValueError("GitHub returned an invalid release tag list")
    targets: list[ForkTarget] = []
    for item in cast(list[object], payload):
        if not isinstance(item, dict):
            continue
        fields = cast(dict[str, object], item)
        name, commit = fields.get("name"), fields.get("commit")
        if isinstance(name, str) and fork_version(name) and isinstance(commit, dict):
            sha = cast(dict[str, object], commit).get("sha")
            if isinstance(sha, str):
                targets.append(ForkTarget(name, sha))
    return targets


async def _latest_fork_tag(channel: str) -> ForkTarget | None:
    targets = _github_targets(await _github_tags())
    eligible = (t for t in targets if channel != "stable" or not Version(t.version).is_prerelease)
    return max(eligible, key=lambda t: Version(t.version), default=None)


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
        if latest is not None and Version(latest.version) > Version(current):
            result["forkTarget"] = asdict(latest)
            result["updateAvailable"] = {
                "currentVersion": current, "latestVersion": latest.version,
                "releaseUrl": release_url(latest.tag),
            }
            status["state"] = "available"
        elif latest is not None:
            status["state"] = (
                "waiting_stable" if selection.channel == "stable" and Version(current).is_prerelease
                else "ahead" if Version(current) > Version(latest.version) else "current"
            )
    except (httpx.HTTPError, ValueError, OSError):
        status["state"] = "unavailable"
    if read_update_channel() != selection:
        raise ValueError("Update channel changed while checking; check again")
    return result
