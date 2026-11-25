from __future__ import annotations

import asyncio
import json
from pathlib import Path

import aiohttp
import pytest
from websockets.asyncio.client import connect

from nanobot.attachments.runtime import clear_attachment_services
from nanobot.bus.queue import MessageBus
from nanobot.channels.websocket.runtime import WebSocketChannel, WebSocketConfig
from nanobot.config.loader import load_config
from nanobot.session.manager import SessionManager
from nanobot.webui.gateway_services import build_gateway_services


@pytest.fixture
async def attachment_gateway(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("nanobot.config.paths.get_data_dir", lambda: tmp_path)
    clear_attachment_services()
    bus = MessageBus()
    config_path = tmp_path / "config.json"
    config = WebSocketConfig(
        port=0,
        path="/ws",
        token="gateway-secret",
        max_message_bytes=1024 * 1024,
    )
    services = build_gateway_services(
        config=config,
        bus=bus,
        session_manager=SessionManager(tmp_path / "workspace", sessions_root=tmp_path / "sessions"),
        static_dist_path=None,
        workspace_path=tmp_path / "workspace",
        default_restrict_to_workspace=True,
        runtime_model_name=None,
        runtime_surface="browser",
        runtime_capabilities_overrides=None,
        config_path=config_path,
    )
    channel = WebSocketChannel(config, bus, gateway=services)
    task = asyncio.create_task(channel.start())
    try:
        async with asyncio.timeout(10):
            while channel._server is None:
                if task.done():
                    await task
                await asyncio.sleep(.01)
        port = channel._server.sockets[0].getsockname()[1]
        yield (
            channel,
            f"http://127.0.0.1:{port}",
            f"ws://127.0.0.1:{port}/ws?token=gateway-secret",
        )
    finally:
        await channel.stop()
        await task
        clear_attachment_services()


async def _event(ws, name: str) -> dict:
    async with asyncio.timeout(5):
        while True:
            payload = json.loads(await ws.recv())
            if payload["event"] == name:
                return payload


async def _mutation(ws, request_id: str, action: str, payload: dict) -> dict:
    await ws.send(json.dumps({
        "type": "webui_request",
        "request_id": request_id,
        "action": action,
        "payload": payload,
    }))
    return await _event(ws, "webui_response")


async def _register_file(channel: WebSocketChannel, name: str = "0123456789ab.bin"):
    path = channel.gateway.attachments.data_dir / "media" / "websocket" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"gateway attachment")
    record = channel.gateway.attachments.register_upload(
        path,
        name=name,
        mime="application/octet-stream",
        session_key="websocket:gateway",
    )
    session = await channel.gateway.session_manager.state.get("websocket:gateway")
    session.metadata["title"] = "gateway"
    session.add_message("user", "file", media=[str(path)])
    await channel.gateway.session_manager.state.finish_turn(session)
    return path, record


async def test_management_http_requires_api_auth(attachment_gateway) -> None:
    channel, http_url, _ = attachment_gateway
    _, record = await _register_file(channel)

    async with aiohttp.ClientSession(trust_env=False) as http:
        unauthorized = await http.get(http_url + "/api/attachments/managed")
        assert unauthorized.status == 401
        await unauthorized.read()

        token = channel.gateway.tokens.issue_api_token(60)
        authorized = await http.get(
            http_url + "/api/attachments/managed",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert authorized.status == 200
        payload = await authorized.json()

    assert payload["items"][0]["id"] == record.id
    assert "files" not in payload["items"][0]
    assert payload["usage"]["managed_bytes"] == len(b"gateway attachment")
    assert payload["settings"] == {
        "max_bytes": None,
        "auto_cleanup": False,
        "orphan_days": 30,
    }


async def test_attachment_mutations_use_contract_and_reject_unknown_ids(
    attachment_gateway,
    tmp_path: Path,
) -> None:
    channel, _, ws_url = attachment_gateway
    path, record = await _register_file(channel)
    token = channel.gateway.tokens.issue_token(300, audience="webui")

    async with connect(ws_url.replace("gateway-secret", token)) as ws:
        await _event(ws, "ready")
        preview = await _mutation(
            ws,
            "preview",
            "attachments.preview_delete",
            {"ids": [record.id]},
        )
        assert preview["ok"] is True
        assert preview["result"]["items"][0]["id"] == record.id
        assert preview["result"]["estimated_freed_bytes"] == len(
            b"gateway attachment",
        )
        assert preview["result"]["affected_sessions"] == [{
            "key": "websocket:gateway",
            "title": "gateway",
        }]

        settings = await _mutation(
            ws,
            "settings",
            "attachments.settings.update",
            {
                "max_bytes": 1048576,
                "auto_cleanup": True,
                "orphan_days": 7,
            },
        )
        assert settings["ok"] is True
        assert settings["result"]["settings"] == {
            "max_bytes": 1048576,
            "auto_cleanup": True,
            "orphan_days": 7,
        }
        saved = load_config(channel.gateway.settings.config.path)
        assert saved.attachments.max_bytes == 1048576
        assert saved.attachments.auto_cleanup is True
        assert saved.attachments.orphan_days == 7

        outside = tmp_path / "outside.txt"
        outside.write_bytes(b"must stay")
        unknown = await _mutation(
            ws,
            "unknown",
            "attachments.delete",
            {"ids": ["../../outside.txt"]},
        )
        assert unknown["ok"] is True
        assert unknown["result"]["deleted"] == []
        assert unknown["result"]["failed"][0]["id"] == "../../outside.txt"
        assert outside.read_bytes() == b"must stay"

        invalid = await _mutation(
            ws,
            "invalid",
            "attachments.delete",
            {"ids": ["valid", 1]},
        )
        assert invalid["ok"] is False
        assert invalid["error"]["status"] == 400

        deleted = await _mutation(
            ws,
            "delete",
            "attachments.delete",
            {"ids": [record.id]},
        )
        assert deleted["ok"] is True
        assert deleted["result"]["deleted"] == [record.id]
        assert deleted["result"]["freed_bytes"] == len(b"gateway attachment")
        assert not path.exists()


async def test_session_delete_reclaims_its_unshared_attachment(
    attachment_gateway,
) -> None:
    channel, _, ws_url = attachment_gateway
    path, record = await _register_file(channel)
    token = channel.gateway.tokens.issue_token(300, audience="webui")

    async with connect(ws_url.replace("gateway-secret", token)) as ws:
        await _event(ws, "ready")
        deleted = await _mutation(
            ws,
            "delete-chat",
            "session.delete",
            {"key": "websocket:gateway"},
        )

    assert deleted["ok"] is True
    assert deleted["result"]["deleted"] is True
    attachments = deleted["result"]["attachments"]
    assert attachments["deleted"] == [record.id]
    assert attachments["failed"] == []
    assert attachments["freed_bytes"] == len(b"gateway attachment")
    assert not path.exists()
