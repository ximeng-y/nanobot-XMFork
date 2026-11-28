import base64
import json
from types import SimpleNamespace
from urllib.parse import quote

import pytest
from websockets.datastructures import Headers
from websockets.http11 import Request

from nanobot.bus.queue import MessageBus
from nanobot.channels.websocket.runtime import WebSocketConfig
from nanobot.webui.gateway_services import build_gateway_services


def gateway(tmp_path):
    return build_gateway_services(
        config=WebSocketConfig(host="127.0.0.1"),
        bus=MessageBus(),
        session_manager=None,
        static_dist_path=None,
        workspace_path=tmp_path / "workspace",
        config_path=tmp_path / "config.json",
        default_restrict_to_workspace=True,
        runtime_model_name=None,
        runtime_surface="browser",
        runtime_capabilities_overrides=None,
    )


def authorized_request(path: str, token: str) -> Request:
    return Request(path, Headers({
        "Host": "127.0.0.1:8765",
        "Authorization": f"Bearer {token}",
    }))


async def get_memory_json(services, path: str, token: str):
    response = await services.http.dispatch(
        SimpleNamespace(remote_address=("127.0.0.1", 12345)),
        authorized_request(path, token),
    )
    payload = json.loads(response.body) if response.status_code == 200 else None
    return response, payload


@pytest.mark.asyncio
async def test_memory_list_uses_agent_workspace_and_is_never_cached(tmp_path):
    services = gateway(tmp_path)
    workspace = tmp_path / "workspace"
    (workspace / "memory" / "wiki" / "topic").mkdir(parents=True)
    (workspace / "SOUL.md").write_text("soul", encoding="utf-8")
    (workspace / "USER.md").write_text("user", encoding="utf-8")
    (workspace / "memory" / "MEMORY.md").write_text("resident", encoding="utf-8")
    (workspace / "memory" / "wiki" / "index.md").write_text("# Wiki", encoding="utf-8")
    (workspace / "memory" / "wiki" / "topic" / "note.md").write_text("# Note", encoding="utf-8")
    (workspace / "memory" / "wiki" / "ignore.txt").write_text("ignored", encoding="utf-8")
    (workspace / "memory" / "history.jsonl").write_text("{}", encoding="utf-8")
    (workspace / "skills").mkdir()
    (workspace / "skills" / "SKILL.md").write_text("# Skill", encoding="utf-8")
    token = services.tokens.issue_api_token(60)

    unauthorized = await services.http.dispatch(
        SimpleNamespace(remote_address=("127.0.0.1", 12345)),
        Request("/api/memory", Headers({"Host": "127.0.0.1:8765"})),
    )
    response, payload = await get_memory_json(services, "/api/memory", token)

    assert unauthorized.status_code == 401
    assert unauthorized.headers["Cache-Control"] == "no-store"
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert [item["path"] for item in payload["documents"]] == [
        "SOUL.md",
        "USER.md",
        "memory/MEMORY.md",
        "memory/wiki/index.md",
        "memory/wiki/topic/note.md",
    ]
    assert payload["imports"] == []
    assert payload["max_import_bytes"] == 262_144


@pytest.mark.asyncio
async def test_memory_document_reads_only_allowed_paths(tmp_path, create_symlink):
    services = gateway(tmp_path)
    workspace = tmp_path / "workspace"
    (workspace / "memory" / "wiki").mkdir(parents=True)
    (workspace / "memory" / "wiki" / "note.md").write_bytes(
        "\ufeff# Allowed\nRelative [link](other.md)\n".encode("utf-8"),
    )
    outside = tmp_path / "outside.md"
    outside.write_text("# Outside", encoding="utf-8")
    create_symlink(workspace / "memory" / "wiki" / "linked.md", outside)
    token = services.tokens.issue_api_token(60)

    response, payload = await get_memory_json(
        services,
        f"/api/memory/document?path={quote('memory/wiki/note.md')}",
        token,
    )
    assert response.status_code == 200
    assert payload == {
        "path": "memory/wiki/note.md",
        "content": "# Allowed\nRelative [link](other.md)\n",
    }

    for path in (
        "../outside.md",
        "memory/wiki/../MEMORY.md",
        "C:/outside.md",
        "memory\\wiki\\note.md",
        "memory/wiki/linked.md",
    ):
        response, _ = await get_memory_json(
            services,
            f"/api/memory/document?path={quote(path)}",
            token,
        )
        assert response.status_code in {400, 404}
        assert response.headers["Cache-Control"] == "no-store"


@pytest.mark.asyncio
async def test_import_preview_and_partial_archive_delete(tmp_path):
    services = gateway(tmp_path)
    token = services.tokens.issue_api_token(60)
    connection = SimpleNamespace(remote_address=("127.0.0.1", 12345))
    first_content = "# First\nRaw source\n"
    second_content = "# Second\n"

    first = await services.http.dispatch_webui_mutation(
        connection,
        "memory.import",
        {
            "name": "first.md",
            "content_base64": base64.b64encode(first_content.encode()).decode(),
        },
    )
    second = await services.http.dispatch_webui_mutation(
        connection,
        "memory.import",
        {
            "name": "second.md",
            "content_base64": base64.b64encode(second_content.encode()).decode(),
        },
    )
    first_id = first.result["entry"]["id"]
    second_id = second.result["entry"]["id"]

    preview_response, preview = await get_memory_json(
        services,
        f"/api/memory/import?id={first_id}",
        token,
    )
    pending_delete = await services.http.dispatch_webui_mutation(
        connection,
        "memory.archive.delete",
        {"ids": [first_id, second_id]},
    )
    archive_response = await services.http.dispatch(
        connection,
        Request("/api/webui/memory/import", Headers({"Host": "127.0.0.1:8765"})),
    )

    assert first.status_code == 200 and second.status_code == 200
    assert preview_response.status_code == 200
    assert preview_response.headers["Cache-Control"] == "no-store"
    assert preview["entry"]["status"] == "pending"
    assert preview["content"] == first_content
    assert pending_delete.result == {"deleted": [], "failed": [first_id, second_id]}
    assert archive_response.status_code == 405

    archived = services.memory.imports.archive(first_id)
    result = await services.http.dispatch_webui_mutation(
        connection,
        "memory.archive.delete",
        {"ids": [first_id, second_id, "invalid"]},
    )

    assert archived["status"] == "archived"
    assert result.result == {"deleted": [first_id], "failed": [second_id, "invalid"]}
    listing_response, listing = await get_memory_json(services, "/api/memory", token)
    assert listing_response.status_code == 200
    assert {entry["id"]: entry["status"] for entry in listing["imports"]} == {
        second_id: "pending",
    }
