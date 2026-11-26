from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote

import aiohttp
import pytest
from websockets.asyncio.client import connect
from websockets.datastructures import Headers
from websockets.http11 import Request

from nanobot.attachments.runtime import clear_attachment_services
from nanobot.bus.queue import MessageBus
from nanobot.channels.websocket.runtime import WebSocketChannel, WebSocketConfig
from nanobot.config.loader import load_config
from nanobot.session.display_history import HistoryFact, HistoryKind
from nanobot.session.manager import Session, SessionManager
from nanobot.webui.gateway_services import build_gateway_services
from nanobot.webui.media_api import b64url_encode, media_path_from_history_signed_url


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


async def _thread_payload(gateway, session_key: str) -> dict:
    api_token = gateway.tokens.issue_api_token(60)
    path = f"/api/sessions/{quote(session_key, safe='')}/webui-thread"
    request = Request(path, Headers([("Authorization", f"Bearer {api_token}")]))
    response = await gateway.http._dispatch_session_routes(request, path)
    assert response.status_code == 200
    return json.loads(response.body)


def _signed_media_parts(url: str) -> tuple[str, str]:
    _, _, _, signature, payload = url.split("/")
    return signature, payload


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


async def test_pending_upload_stays_out_of_managed_usage_and_can_commit(
    attachment_gateway,
) -> None:
    channel, http_url, ws_url = attachment_gateway
    raw = b"pending attachment"
    channel.gateway.attachments.update_policy(
        max_bytes=len(raw),
        auto_cleanup=False,
        orphan_days=30,
    )
    ws_token = channel.gateway.tokens.issue_token(300, audience="webui")
    api_token = channel.gateway.tokens.issue_api_token(60)

    async with (
        connect(ws_url.replace("gateway-secret", ws_token)) as ws,
        aiohttp.ClientSession(trust_env=False) as http,
    ):
        ready = await _event(ws, "ready")
        capability = ready["upload"]
        headers = {
            "Authorization": f"Bearer {capability['token']}",
            "Content-Type": "text/plain",
            "X-Attachment-Name": "pending.txt",
        }
        async with http.post(http_url + capability["path"], data=raw, headers=headers) as response:
            assert response.status == 201, await response.text()
            reference = (await response.json())["reference"]

        pending = await http.get(
            http_url + "/api/attachments/managed",
            headers={"Authorization": f"Bearer {api_token}"},
        )
        assert pending.status == 200
        pending_payload = await pending.json()
        assert pending_payload["items"] == []
        assert pending_payload["usage"] == {
            "managed_bytes": 0,
            "pending_bytes": len(raw),
            "protected_bytes": 0,
        }

        await ws.send(json.dumps({
            "type": "message",
            "chat_id": ready["chat_id"],
            "content": "send pending file",
            "webui": True,
            "turn_id": "pending-turn",
            "media": [{"reference": reference, "name": "pending.txt"}],
        }))
        assert (await _event(ws, "message_accepted"))["turn_id"] == "pending-turn"

        committed = await http.get(
            http_url + "/api/attachments/managed",
            headers={"Authorization": f"Bearer {api_token}"},
        )
        assert committed.status == 200
        committed_payload = await committed.json()
        assert len(committed_payload["items"]) == 1
        assert committed_payload["usage"]["managed_bytes"] == len(raw)
        assert committed_payload["usage"]["pending_bytes"] == 0


async def test_session_history_does_not_restage_deleted_markdown_copy(
    attachment_gateway,
    tmp_path: Path,
) -> None:
    channel, http_url, _ = attachment_gateway
    source = tmp_path / "workspace" / "chart.png"
    source.write_bytes(b"\x89PNG\r\n\x1a\nhistory")
    markdown = "chart below\n\n![chart](chart.png)"
    session_key = "websocket:history"
    session = await channel.gateway.session_manager.state.get(session_key)
    session.metadata["title"] = "history"
    await channel.gateway.session_manager.state.append_history(
        session_key,
        [
            HistoryFact(HistoryKind.INPUT, {"text": "show chart"}),
            HistoryFact(HistoryKind.ANSWER, {"text": markdown}, turn_id="history-turn"),
            HistoryFact(HistoryKind.COMPLETION, turn_id="history-turn"),
        ],
        expected_generation=session.generation,
    )
    api_token = channel.gateway.tokens.issue_api_token(60)
    headers = {"Authorization": f"Bearer {api_token}"}
    path = f"/api/sessions/{quote(session_key, safe='')}/webui-thread"

    async with aiohttp.ClientSession(trust_env=False) as http:
        first = await http.get(http_url + path, headers=headers)
        assert first.status == 200
        first_payload = await first.json()
        first_text = next(
            event["text"] for event in first_payload["events"]
            if event.get("event") == "message" and "chart" in event.get("text", "")
        )
        assert "/api/media/" in first_text

        record = channel.gateway.attachments.find_outbound_for_source_path(
            source,
            session_key,
            include_deleted=True,
        )
        assert record is not None
        channel.gateway.attachments.delete([record.id])

        second = await http.get(http_url + path, headers=headers)
        assert second.status == 200
        second_payload = await second.json()
        second_event = next(
            event for event in second_payload["events"]
            if event.get("event") == "message" and "chart below" in event.get("text", "")
        )
        second_text = second_event["text"]

    assert "chart below" in second_text
    assert "chart" in second_text
    assert "![chart]" not in second_text
    assert "/api/media/" not in second_text
    assert second_event.get("media_urls") == [{
        "kind": "image",
        "name": "chart.png",
        "managed_id": record.id,
        "deleted": True,
        "url": "",
    }]
    assert source.exists()
    assert not list(
        (channel.gateway.attachments.data_dir / "media" / "websocket").glob("*-chart.png"),
    )


async def test_subagent_history_passes_session_key_to_markdown_projection(
    attachment_gateway,
    tmp_path: Path,
) -> None:
    channel, http_url, _ = attachment_gateway
    source = tmp_path / "workspace" / "subagent-chart.png"
    source.write_bytes(b"\x89PNG\r\n\x1a\nsubagent")
    markdown = "subagent chart\n\n![subagent](subagent-chart.png)"
    parent_key = "websocket:parent"
    child = Session(
        key="websocket:child",
        messages=[{
            "role": "assistant",
            "content": markdown,
            "timestamp": "2026-01-01T00:00:00+00:00",
        }],
    )

    class _SubagentReader:
        @staticmethod
        def read_session(task_id: str, owner: str):
            assert task_id == "task-1"
            assert owner == parent_key
            return (
                SimpleNamespace(state="done", started_at=0.0, finished_at=1.0),
                child,
            )

    channel.gateway.http.subagent_manager = _SubagentReader()
    api_token = channel.gateway.tokens.issue_api_token(60)
    headers = {"Authorization": f"Bearer {api_token}"}
    path = (
        f"/api/sessions/{quote(parent_key, safe='')}"
        "/subagents/task-1/webui-thread"
    )

    async with aiohttp.ClientSession(trust_env=False) as http:
        first = await http.get(http_url + path, headers=headers)
        assert first.status == 200
        first_payload = await first.json()
        first_messages = [
            event["text"] for event in first_payload["events"]
            if event.get("event") == "message" and "/api/media/" in event.get("text", "")
        ]
        assert first_messages, first_payload
        first_text = first_messages[0]
        assert "/api/media/" in first_text

        record = channel.gateway.attachments.find_outbound_for_source_path(
            source,
            parent_key,
            include_deleted=True,
        )
        assert record is not None
        channel.gateway.attachments.delete([record.id])

        second = await http.get(http_url + path, headers=headers)
        assert second.status == 200
        second_payload = await second.json()
        second_events = [
            event for event in second_payload["events"]
            if event.get("event") == "message" and "subagent chart" in event.get("text", "")
        ]
        assert second_events, second_payload
        second_event = second_events[0]
        second_text = second_event["text"]

    assert "subagent chart" in second_text
    assert "subagent" in second_text
    assert "![subagent]" not in second_text
    assert "/api/media/" not in second_text
    assert second_event.get("media_urls") == [{
        "kind": "image",
        "name": "subagent-chart.png",
        "managed_id": record.id,
        "deleted": True,
        "url": "",
    }]
    assert source.exists()
    assert not list(
        (channel.gateway.attachments.data_dir / "media" / "websocket").glob(
            "*-subagent-chart.png",
        ),
    )


async def test_stream_end_with_signed_markdown_projects_deleted_media(
    attachment_gateway,
    tmp_path: Path,
) -> None:
    channel, http_url, _ = attachment_gateway
    source = tmp_path / "workspace" / "signed-chart.png"
    source.write_bytes(b"\x89PNG\r\n\x1a\nsigned")
    session_key = "websocket:signed-stream"
    signed = channel.gateway.media.sign_or_stage_media_path(
        source, session_key=session_key,
    )
    assert signed is not None and signed["url"]
    markdown = f"stream chart\n\n![signed]({signed['url']})"
    session = await channel.gateway.session_manager.state.get(session_key)
    session.metadata["title"] = "signed stream"
    await channel.gateway.session_manager.state.append_history(
        session_key,
        [
            HistoryFact(HistoryKind.INPUT, {"text": "show stream chart"}),
            HistoryFact(
                HistoryKind.ANSWER_SEGMENT,
                {"text": markdown},
                turn_id="signed-stream-turn",
            ),
            HistoryFact(HistoryKind.COMPLETION, turn_id="signed-stream-turn"),
        ],
        expected_generation=session.generation,
    )
    record = channel.gateway.attachments.find_outbound_for_source_path(
        source, session_key, include_deleted=True,
    )
    assert record is not None
    channel.gateway.attachments.delete([record.id])

    api_token = channel.gateway.tokens.issue_api_token(60)
    path = f"/api/sessions/{quote(session_key, safe='')}/webui-thread"
    async with aiohttp.ClientSession(trust_env=False) as http:
        response = await http.get(
            http_url + path,
            headers={"Authorization": f"Bearer {api_token}"},
        )
        assert response.status == 200
        payload = await response.json()

    event = next(
        event for event in payload["events"]
        if event.get("event") == "stream_end" and "stream chart" in event.get("text", "")
    )
    assert "signed" in event["text"]
    assert "/api/media/" not in event["text"]
    assert event["media_urls"] == [{
        "kind": "image",
        "name": "signed-chart.png",
        "managed_id": record.id,
        "deleted": True,
        "url": "",
    }]
    assert source.exists()
    assert not signed["url"] in event["text"]


@pytest.mark.asyncio
async def test_signed_stream_history_reprojects_after_gateway_restart(
    attachment_gateway,
    tmp_path: Path,
) -> None:
    channel, _, _ = attachment_gateway
    gateway = channel.gateway
    workspace = tmp_path / "workspace"
    available_source = workspace / "available-chart.png"
    shared_source = workspace / "shared-chart.png"
    available_source.write_bytes(b"\x89PNG\r\n\x1a\navailable")
    shared_source.write_bytes(b"\x89PNG\r\n\x1a\nshared")
    session_key = "websocket:signed-restart"
    other_session_key = "websocket:signed-restart-other"

    available_signed = gateway.media.sign_or_stage_media_path(
        available_source, session_key=session_key,
    )
    shared_signed = gateway.media.sign_or_stage_media_path(
        shared_source, session_key=session_key,
    )
    other_shared_signed = gateway.media.sign_or_stage_media_path(
        shared_source, session_key=other_session_key,
    )
    assert available_signed is not None
    assert shared_signed is not None
    assert other_shared_signed is not None

    available_record = gateway.attachments.find_outbound_for_source_path(
        available_source, session_key, include_deleted=True,
    )
    shared_record = gateway.attachments.find_outbound_for_source_path(
        shared_source, session_key, include_deleted=True,
    )
    other_shared_record = gateway.attachments.find_outbound_for_source_path(
        shared_source, other_session_key, include_deleted=True,
    )
    assert available_record is not None
    assert shared_record is not None
    assert other_shared_record is not None

    markdown = (
        "restart chart\n\n"
        f"![available]({available_signed['url']})\n"
        f"![shared]({shared_signed['url']})"
    )
    session = await gateway.session_manager.state.get(session_key)
    session.metadata["title"] = "signed restart"
    await gateway.session_manager.state.append_history(
        session_key,
        [
            HistoryFact(HistoryKind.INPUT, {"text": "show restart charts"}),
            HistoryFact(
                HistoryKind.ANSWER_SEGMENT,
                {"text": markdown},
                turn_id="signed-restart-turn",
            ),
            HistoryFact(HistoryKind.COMPLETION, turn_id="signed-restart-turn"),
        ],
        expected_generation=session.generation,
    )
    other_markdown = f"other chart\n\n![shared]({other_shared_signed['url']})"
    other_session = await gateway.session_manager.state.get(other_session_key)
    other_session.metadata["title"] = "signed restart other"
    await gateway.session_manager.state.append_history(
        other_session_key,
        [
            HistoryFact(HistoryKind.INPUT, {"text": "show other chart"}),
            HistoryFact(
                HistoryKind.ANSWER_SEGMENT,
                {"text": other_markdown},
                turn_id="signed-restart-other-turn",
            ),
            HistoryFact(
                HistoryKind.COMPLETION, turn_id="signed-restart-other-turn",
            ),
        ],
        expected_generation=other_session.generation,
    )

    gateway.attachments.delete([shared_record.id])
    shared_path = gateway.attachments.store.resolve_relative(
        shared_record.files[0].relative_path,
    )
    records_before = {
        path: [record.id for record in gateway.attachments.store.records_for_path(path)]
        for path in (
            gateway.attachments.store.resolve_relative(
                available_record.files[0].relative_path,
            ),
            shared_path,
        )
    }
    media_root = gateway.attachments.data_dir / "media"
    files_before = sorted(
        str(path.relative_to(media_root))
        for path in media_root.rglob("*")
        if path.is_file()
    )
    assert shared_path.exists()

    clear_attachment_services()
    restarted = build_gateway_services(
        config=WebSocketConfig(
            port=0,
            path="/ws",
            token="gateway-secret",
            max_message_bytes=1024 * 1024,
        ),
        bus=MessageBus(),
        session_manager=gateway.session_manager,
        static_dist_path=None,
        workspace_path=workspace,
        default_restrict_to_workspace=True,
        runtime_model_name=None,
        runtime_surface="browser",
        runtime_capabilities_overrides=None,
        config_path=tmp_path / "config.json",
    )
    assert restarted.media.secret != gateway.media.secret

    payload = await _thread_payload(restarted, session_key)
    event = next(
        event for event in payload["events"]
        if event.get("event") == "stream_end" and "restart chart" in event.get("text", "")
    )
    assert available_signed["url"] not in event["text"]
    assert shared_signed["url"] not in event["text"]
    assert "![available](" in event["text"]
    assert "shared" in event["text"]
    assert event["media_urls"] == [{
        "kind": "image",
        "name": "shared-chart.png",
        "managed_id": shared_record.id,
        "deleted": True,
        "url": "",
    }]
    current_url = event["text"].split("![available](", 1)[1].split(")", 1)[0]
    assert current_url != available_signed["url"]
    assert restarted.media.serve_signed_media(
        *_signed_media_parts(current_url),
    ).status_code == 200
    assert restarted.media.serve_signed_media(
        *_signed_media_parts(available_signed["url"]),
    ).status_code == 401
    assert restarted.media.serve_signed_media(
        *_signed_media_parts(shared_signed["url"]),
    ).status_code == 401

    other_payload = await _thread_payload(restarted, other_session_key)
    other_event = next(
        event for event in other_payload["events"]
        if event.get("event") == "stream_end" and "other chart" in event.get("text", "")
    )
    assert other_shared_signed["url"] not in other_event["text"]
    assert "![shared](" in other_event["text"]
    assert "media_urls" not in other_event
    other_current_url = other_event["text"].split("![shared](", 1)[1].split(")", 1)[0]
    assert restarted.media.serve_signed_media(
        *_signed_media_parts(other_current_url),
    ).status_code == 200

    assert {
        path: [record.id for record in restarted.attachments.store.records_for_path(path)]
        for path in records_before
    } == records_before
    files_after = sorted(
        str(path.relative_to(media_root))
        for path in media_root.rglob("*")
        if path.is_file()
    )
    assert files_after == files_before


async def test_deleted_markdown_media_keeps_other_session_reference(
    attachment_gateway,
    tmp_path: Path,
) -> None:
    channel, http_url, _ = attachment_gateway
    source = tmp_path / "workspace" / "shared-chart.png"
    source.write_bytes(b"\x89PNG\r\n\x1a\nshared")
    markdown = "shared chart\n\n![shared](shared-chart.png)"
    first_key = "websocket:shared-first"
    second_key = "websocket:shared-second"
    for index, session_key in enumerate((first_key, second_key)):
        session = await channel.gateway.session_manager.state.get(session_key)
        session.metadata["title"] = f"shared-{index}"
        await channel.gateway.session_manager.state.append_history(
            session_key,
            [
                HistoryFact(HistoryKind.INPUT, {"text": "show shared chart"}),
                HistoryFact(
                    HistoryKind.ANSWER,
                    {"text": markdown},
                    turn_id=f"shared-turn-{index}",
                ),
                HistoryFact(HistoryKind.COMPLETION, turn_id=f"shared-turn-{index}"),
            ],
            expected_generation=session.generation,
        )

    api_token = channel.gateway.tokens.issue_api_token(60)
    headers = {"Authorization": f"Bearer {api_token}"}
    paths = {
        key: f"/api/sessions/{quote(key, safe='')}/webui-thread"
        for key in (first_key, second_key)
    }
    async with aiohttp.ClientSession(trust_env=False) as http:
        for key in (first_key, second_key):
            response = await http.get(http_url + paths[key], headers=headers)
            assert response.status == 200
        first_record = channel.gateway.attachments.find_outbound_for_source_path(
            source, first_key, include_deleted=True,
        )
        second_record = channel.gateway.attachments.find_outbound_for_source_path(
            source, second_key, include_deleted=True,
        )
        assert first_record is not None and second_record is not None
        channel.gateway.attachments.delete([first_record.id])

        first_response = await http.get(http_url + paths[first_key], headers=headers)
        second_response = await http.get(http_url + paths[second_key], headers=headers)
        first_payload = await first_response.json()
        second_payload = await second_response.json()

    first_event = next(
        event for event in first_payload["events"]
        if event.get("event") == "message" and "shared chart" in event.get("text", "")
    )
    second_event = next(
        event for event in second_payload["events"]
        if event.get("event") == "message" and "shared chart" in event.get("text", "")
    )
    assert first_event["media_urls"] == [{
        "kind": "image",
        "name": "shared-chart.png",
        "managed_id": first_record.id,
        "deleted": True,
        "url": "",
    }]
    assert "/api/media/" in second_event["text"]
    assert "media_urls" not in second_event
    assert source.exists()


def test_history_signed_media_path_only_returns_media_relative_candidates(
    tmp_path: Path,
) -> None:
    media_root = tmp_path / "media"
    media_root.mkdir()
    valid_payload = b64url_encode(b"websocket/chart.png")
    valid_url = f"/api/media/old-signature/{valid_payload}"
    assert media_path_from_history_signed_url(
        valid_url, media_dir=lambda _channel: media_root,
    ) == (media_root / "websocket" / "chart.png").resolve()

    outside = tmp_path / "outside.png"
    invalid_payloads = (
        str(outside),
        "../outside.png",
        "\0" + json.dumps(["websocket:session", str(outside)]),
    )
    for payload_text in invalid_payloads:
        url = f"/api/media/old-signature/{b64url_encode(payload_text.encode())}"
        assert media_path_from_history_signed_url(
            url, media_dir=lambda _channel: media_root,
        ) is None


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
