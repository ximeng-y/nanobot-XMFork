from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

import nanobot
import nanobot.webui.version_check as version_check
from nanobot.repository import TAGS_URL
from nanobot.update_channel import UpdateChannelRequest, read_update_channel
from nanobot.update_service import UpdateJobService


@pytest.fixture
def releases(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "account"))
    monkeypatch.setattr(sys, "prefix", str(tmp_path / "installation"))
    monkeypatch.setattr(nanobot, "__version__", "0.4.2+xm.1")
    state = SimpleNamespace(
        tags=["v0.4.2-xm.1"],
        offline=False,
        malformed=False,
        changed_channel=None,
        requests=[],
    )

    def handler(request):
        state.requests.append(str(request.url))
        if state.offline:
            raise httpx.ConnectError("private proxy credential", request=request)
        if state.malformed:
            return httpx.Response(200, json={"tags": []})
        if state.changed_channel is not None:
            select(state.changed_channel)
        return httpx.Response(200, json=[
            {"name": tag, "commit": {"sha": "a" * 40}} for tag in state.tags
        ])

    monkeypatch.setattr(
        version_check, "PinnedDNSAsyncTransport", lambda: httpx.MockTransport(handler),
    )
    return state


def select(channel):
    current = read_update_channel()
    return UpdateJobService().change_channel(
        UpdateChannelRequest(channel=channel, revision=current.revision)
    )


@pytest.mark.parametrize(
    ("tag", "expected"),
    [
        ("v0.4.2-xm.1", "0.4.2+xm.1"),
        ("v0.4.3.dev1-xm.2", "0.4.3.dev1+xm.2"),
        ("v0.4.2+xm.1", None),
        ("v0.4.3", None),
        ("nightly", None),
    ],
)
def test_release_version_accepts_only_fork_tags(tag, expected):
    from nanobot.repository import fork_version
    assert fork_version(tag) == expected


async def test_current_fork_tag_is_up_to_date_and_ignores_upstream_tags(releases):
    releases.tags.extend(["v0.4.3", "v0.4.3.dev1"])
    result = await version_check.check_for_update()
    assert result["updateAvailable"] is None
    assert result["releaseStatus"]["state"] == "current"
    assert releases.requests == [TAGS_URL]


async def test_newer_fork_tag_is_offered_with_its_release_url(releases):
    releases.tags.append("v0.4.3-xm.2")
    result = await version_check.check_for_update()
    assert result["forkTarget"] == {"tag": "v0.4.3-xm.2", "commit": "a" * 40}
    assert result["updateAvailable"] == {
        "currentVersion": "0.4.2+xm.1",
        "latestVersion": "0.4.3+xm.2",
        "releaseUrl": "https://github.com/ximeng-y/nanobot-XMFork/releases/tag/v0.4.3-xm.2",
    }
    assert result["releaseStatus"]["state"] == "available"


async def test_preview_tags_are_only_visible_on_the_preview_channel(releases):
    releases.tags.append("v0.4.3.dev1-xm.1")
    assert (await version_check.check_for_update())["releaseStatus"]["state"] == "current"
    select("preview")
    result = await version_check.check_for_update()
    assert result["updateAvailable"]["latestVersion"] == "0.4.3.dev1+xm.1"
    assert result["releaseStatus"]["channel"] == "preview"


async def test_preview_waits_for_stable_and_reports_ahead(releases, monkeypatch):
    monkeypatch.setattr(nanobot, "__version__", "0.4.3.dev1+xm.1")
    assert (await version_check.check_for_update())["releaseStatus"]["state"] == "waiting_stable"
    select("preview")
    result = await version_check.check_for_update()
    assert result["updateAvailable"] is None
    assert result["releaseStatus"]["state"] == "ahead"


async def test_unavailable_check_hides_errors_and_can_recover(releases):
    releases.offline = True
    result = await version_check.check_for_update()
    assert result["updateAvailable"] is None
    assert result["releaseStatus"]["state"] == "unavailable"
    assert "credential" not in str(result)
    releases.offline = False
    assert (await version_check.check_for_update())["releaseStatus"]["state"] == "current"


async def test_invalid_github_payload_is_unavailable(releases):
    releases.malformed = True
    result = await version_check.check_for_update()
    assert result["updateAvailable"] is None
    assert result["releaseStatus"]["state"] == "unavailable"


async def test_channel_change_during_check_requires_another_check(releases):
    releases.changed_channel = "preview"
    with pytest.raises(ValueError, match="changed"):
        await version_check.check_for_update()
