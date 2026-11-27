from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from nanobot.bus.queue import MessageBus
from nanobot.channels.websocket.attachment_policy import AttachmentIngressLimits
from nanobot.channels.websocket.runtime import UploadConfig, WebSocketConfig
from nanobot.config.loader import load_config, save_config
from nanobot.config.schema import Config
from nanobot.session.manager import SessionManager
from nanobot.webui.gateway_services import build_gateway_services


def test_upload_config_defaults_and_partial_alias_input() -> None:
    assert UploadConfig().model_dump(by_alias=True) == {
        "maxCount": 4,
        "maxFileBytes": 6_291_456,
        "maxTotalBytes": 25_165_824,
        "maxVideoCount": 1,
        "maxVideoBytes": 20_971_520,
        "maxPending": 128,
        "maxPendingBytes": 134_217_728,
    }
    websocket = WebSocketConfig()
    assert websocket.uploads == UploadConfig()
    assert websocket.model_dump(by_alias=True)["uploads"]["maxVideoCount"] == 1

    config = UploadConfig.model_validate({
        "maxCount": 7,
        "maxFileBytes": 8_000_000,
        "maxTotalBytes": 9_000_000,
        "maxVideoCount": 3,
        "maxVideoBytes": 10_000_000,
        "maxPending": 11,
        "maxPendingBytes": 12_000_000,
    })
    assert config.max_count == 7
    assert config.max_video_count == 3
    assert config.max_pending_bytes == 12_000_000


@pytest.mark.parametrize("field", [
    "maxCount",
    "maxFileBytes",
    "maxTotalBytes",
    "maxVideoCount",
    "maxVideoBytes",
    "maxPending",
    "maxPendingBytes",
])
@pytest.mark.parametrize("value", [0, -1, True, 1.5, "1"])
def test_upload_config_requires_positive_integers(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        UploadConfig.model_validate({field: value})


def test_upload_config_round_trips_through_config_file(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    values = {
        "maxCount": 2,
        "maxFileBytes": 3_000_000,
        "maxTotalBytes": 4_000_000,
        "maxVideoCount": 2,
        "maxVideoBytes": 5_000_000,
        "maxPending": 6,
        "maxPendingBytes": 7_000_000,
    }
    save_config(Config(channels={"websocket": {"uploads": values}}), path)

    loaded = load_config(path)
    section = getattr(loaded.channels, "websocket")
    assert section["uploads"] == values
    parsed = WebSocketConfig.model_validate(section)
    assert parsed.uploads.model_dump(by_alias=True) == values


def test_build_gateway_services_uses_upload_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("nanobot.config.paths.get_data_dir", lambda: tmp_path)
    config = WebSocketConfig(
        port=0,
        path="/ws",
        token="test-secret",
        uploads={
            "maxCount": 2,
            "maxFileBytes": 3,
            "maxTotalBytes": 4,
            "maxVideoCount": 2,
            "maxVideoBytes": 5,
            "maxPending": 6,
            "maxPendingBytes": 7,
        },
    )
    services = build_gateway_services(
        config=config,
        bus=MessageBus(),
        session_manager=SessionManager(tmp_path),
        static_dist_path=None,
        workspace_path=tmp_path,
        default_restrict_to_workspace=True,
        runtime_model_name=None,
        runtime_surface="browser",
        runtime_capabilities_overrides=None,
        config_path=tmp_path / "config.json",
    )

    assert services.ingress.attachments == AttachmentIngressLimits(
        max_count=2,
        max_file_bytes=3,
        max_total_bytes=4,
        max_video_count=2,
        max_video_bytes=5,
    )
    assert services.uploads.store.max_pending == 6
    assert services.uploads.store.max_pending_bytes == 7
