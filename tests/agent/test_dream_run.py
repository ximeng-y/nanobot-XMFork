import asyncio
import base64
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from nanobot.agent.context_governance import ContextWindowExceededError
from nanobot.agent.dream import run_dream
from nanobot.agent.memory import MemoryStore
from nanobot.bus.events import OutboundMessage


def response(reason="completed", content="done"):
    return OutboundMessage(
        channel="cli", chat_id="dream", content=content, metadata={"_stop_reason": reason},
    )


def import_markdown(store, name: str, content: str):
    return store.imports.import_bytes(
        name,
        base64.b64encode(content.encode("utf-8")).decode("ascii"),
    )


def import_id_from_prompt(prompt: str) -> str:
    marker = "File ID: `"
    return prompt.split(marker, 1)[1].split("`", 1)[0]


def loop_for(store, process):
    return SimpleNamespace(
        context=SimpleNamespace(memory=store),
        sessions=SimpleNamespace(state=SimpleNamespace(prune_dream_sessions=AsyncMock())),
        dream_runtime=lambda: None,
        process_direct=process,
    )


def test_foreground_work_check_ignores_unrelated_background_tasks():
    from nanobot.agent.loop import AgentLoop

    loop = object.__new__(AgentLoop)
    loop.bus = SimpleNamespace(inbound_size=0)
    loop._pending_queues = {}
    loop._active_tasks = {}
    loop._background_tasks = {object()}

    assert loop.has_foreground_work() is False

    loop.bus.inbound_size = 1
    assert loop.has_foreground_work() is True
    loop.bus.inbound_size = 0
    loop._pending_queues = {"user:1": object()}
    assert loop.has_foreground_work() is True
    loop._pending_queues = {}
    loop._active_tasks = {"user:1": {object()}}
    assert loop.has_foreground_work() is True


def test_import_completion_marker_must_be_the_last_nonempty_line():
    import_id = "a" * 32
    marker = MemoryStore.import_completion_marker(import_id)

    assert MemoryStore.dream_import_completed(
        response(content=f"Memory checked.\n{marker}\n"),
        import_id,
    )
    assert not MemoryStore.dream_import_completed(
        response(content=f"{marker}\nmore text"),
        import_id,
    )


@pytest.mark.asyncio
async def test_one_dream_reads_full_pages_and_defers_new_history(tmp_path):
    from nanobot.agent.loop import AgentLoop
    from nanobot.bus.queue import MessageBus
    from nanobot.providers.base import GenerationSettings, LLMResponse, ToolCallRequest

    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    provider.supports_tools = True
    provider.generation = GenerationSettings()
    provider.estimate_prompt_tokens.return_value = (200_000, "test-provider")
    loop = AgentLoop(
        bus=MessageBus(), provider=provider, workspace=tmp_path,
        context_window_tokens=512_000,
    )
    store = loop.context.memory
    markers = ("DREAM_SOUL_MARKER", "DREAM_USER_MARKER", "DREAM_MEMORY_MARKER", "DREAM_AGENTS_MARKER")
    store.write_soul(markers[0])
    store.write_user(markers[1])
    store.write_memory(markers[2])
    (tmp_path / "AGENTS.md").write_text(markers[3], encoding="utf-8")
    store.git.init()
    checkpoints = ["Project knowledge. " * 3000 + f"TAIL-{i}" for i in range(3)]
    for checkpoint in checkpoints:
        store.append_history(checkpoint)
    for index in range(23):
        store.append_history(f"queued record {index}")
    model_calls = 0
    read_records = []

    def call(name, **arguments):
        return LLMResponse(content=None, tool_calls=[ToolCallRequest(
            id=f"call-{model_calls}", name=name, arguments=arguments,
        )], finish_reason="tool_calls")

    def collect_read(messages):
        text = next(message["content"] for message in reversed(messages) if message["role"] == "tool")
        for line in text.splitlines():
            number, separator, encoded = line.partition("| ")
            if separator and number.isdigit():
                read_records.append(json.loads(encoded))

    async def chat(**kwargs):
        nonlocal model_calls
        model_calls += 1
        messages = kwargs["messages"]
        assert store.get_last_dream_cursor() == 0
        if model_calls == 1:
            task = messages[-1]["content"]
            assert str(store.history_file.resolve()) in task
            assert "Read lines 1-26" in task and "0 < cursor <= 26" in task
            request = "\n".join(str(message.get("content", "")) for message in messages)
            for marker in markers:
                assert marker in messages[0]["content"] and request.count(marker) == 1
            assert "TAIL-0" not in request and "queued record 22" not in request
            store.append_history("Arrived while Dream was running")
            return call("read_file", path=str(store.history_file), offset=1, limit=26)
        if model_calls == 2:
            collect_read(messages)
            assert [entry["content"] for entry in read_records] == checkpoints[:2]
            return call("read_file", path=str(store.history_file), offset=3, limit=24)
        if model_calls == 3:
            collect_read(messages)
            assert [entry["cursor"] for entry in read_records] == list(range(1, 27))
            assert read_records[2]["content"] == checkpoints[2]
            assert read_records[-1]["content"] == "queued record 22"
            return call("write_file", path=str(store.wiki_dir / "index.md"), content="# Wiki\nCurrent knowledge")
        return LLMResponse(content="History range consolidated.")

    provider.chat_stream_with_retry = AsyncMock(side_effect=chat)
    try:
        result = await run_dream(loop, commit_prefix="dream: periodic memory consolidation")
        assert result.error is None
        assert model_calls == 4
        assert store.get_last_dream_cursor() == 26
        assert result.commit_sha
        commit, diff = store.git.show_commit_diff(result.commit_sha)
        assert commit.message.startswith("dream: periodic memory consolidation\n\n")
        assert "memory/wiki/index.md" in diff and "Current knowledge" in commit.message
        assert ".dream_cursor" not in commit.message
        assert (store.wiki_dir / "index.md").read_text(encoding="utf-8") == "# Wiki\nCurrent knowledge"
        sessions = await loop.sessions.state.list_sessions()
        assert len(sessions) == 1
        assert loop.sessions.read_session_snapshot(sessions[0]["key"]) is not None
        next_prompt, next_cursor = store.build_dream_prompt()
        assert "Read lines 27-27" in next_prompt
        assert next_cursor == 27
    finally:
        await loop.sessions.state.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["length", "exception"])
async def test_failed_task_keeps_entire_range_pending_after_restart(tmp_path, failure):
    store = MemoryStore(tmp_path, max_history_entries=5)
    store.git.init()
    for index in range(25):
        store.append_history(f"record-{index:02d}")

    async def process(prompt, **kwargs):
        assert "Read lines 1-25" in prompt
        store.write_memory("# Memory\nPartial update")
        if failure == "exception":
            raise RuntimeError("Provider unavailable")
        return response(failure)

    result = await run_dream(loop_for(store, process), commit_prefix="dream: manual run")
    assert result.error
    assert store.get_last_dream_cursor() == 0
    assert len(store.read_unprocessed_history(0)) == 25
    assert result.commit_sha
    assert store.read_memory() == "# Memory\nPartial update"

    restarted = MemoryStore(tmp_path)
    resumed = []

    async def finish(prompt, **kwargs):
        resumed.append(prompt)
        return response()

    result = await run_dream(loop_for(restarted, finish), commit_prefix="dream: manual run")
    assert result.error is None
    assert restarted.get_last_dream_cursor() == 25
    assert len(resumed) == 1
    assert "Read lines 1-25" in resumed[0]


@pytest.mark.asyncio
async def test_overlapping_manual_and_periodic_runs_do_not_repeat_history(tmp_path):
    store = MemoryStore(tmp_path)
    store.append_history("One pending record")
    entered, release, second_started = asyncio.Event(), asyncio.Event(), asyncio.Event()
    calls = []

    async def process(prompt, **kwargs):
        calls.append(prompt)
        entered.set()
        await release.wait()
        return response()

    loop = loop_for(store, process)
    first = asyncio.create_task(run_dream(loop, commit_prefix="dream: periodic memory consolidation"))
    await entered.wait()

    async def manual():
        second_started.set()
        return await run_dream(loop, commit_prefix="dream: manual run")

    second = asyncio.create_task(manual())
    try:
        await second_started.wait()
        assert len(calls) == 1
    finally:
        release.set()
        outcomes = await asyncio.gather(first, second)
    assert len(calls) == 1
    assert all(outcome.error is None for outcome in outcomes)
    assert [outcome.had_input for outcome in outcomes] == [True, False]
    assert store.get_last_dream_cursor() == 1


@pytest.mark.asyncio
async def test_imported_files_are_archived_only_after_independent_confirmation(tmp_path):
    store = MemoryStore(tmp_path)
    store.git.init()
    changed = import_markdown(store, "changed.md", "# Changed\nDurable fact")
    unchanged = import_markdown(store, "unchanged.md", "# Nothing\nNo durable fact")
    failed = import_markdown(store, "failed.md", "# Failed\nKeep the original")

    async def process(prompt, **kwargs):
        import_id = import_id_from_prompt(prompt)
        if "Durable fact" in prompt:
            store.write_memory("# Memory\nDurable fact")
            return response(content=f"DREAM-IMPORT-COMPLETE:{import_id}")
        if "No durable fact" in prompt:
            return response(content=f"DREAM-IMPORT-COMPLETE:{import_id}")
        return response(content="Finished without confirming the import")

    result = await run_dream(loop_for(store, process), commit_prefix="dream: manual run")

    entries = {entry["id"]: entry for entry in store.imports.list_entries()}
    assert result.had_input is True
    assert result.changed is True
    assert result.error and "1 imported file failed" in result.error
    assert entries[changed["id"]]["status"] == "archived"
    assert entries[unchanged["id"]]["status"] == "archived"
    assert entries[failed["id"]]["status"] == "failed"
    assert entries[failed["id"]]["error"]
    assert store.imports.read_import(failed["id"])[1] == "# Failed\nKeep the original"
    assert store.read_memory() == "# Memory\nDurable fact"
    assert result.commit_sha
    _, diff = store.git.show_commit_diff(result.commit_sha)
    assert "memory/MEMORY.md" in diff and "Durable fact" in diff
    assert "content.md" not in diff
    assert store.get_last_dream_cursor() == 0


@pytest.mark.asyncio
async def test_unconfirmed_import_retries_with_full_text_then_archives(tmp_path):
    store = MemoryStore(tmp_path)
    imported = import_markdown(
        store,
        "retry.md",
        "# Retry\nFULL-CONTENT-TAIL\nNo durable change",
    )
    prompts = []

    async def unconfirmed(prompt, **kwargs):
        prompts.append(prompt)
        return response(content="I stopped early")

    first = await run_dream(loop_for(store, unconfirmed), commit_prefix="dream: manual run")

    assert first.error and "1 imported file failed" in first.error
    assert store.imports.list_entries()[0]["status"] == "failed"

    async def confirmed(prompt, **kwargs):
        prompts.append(prompt)
        assert "FULL-CONTENT-TAIL" in prompt
        import_id = import_id_from_prompt(prompt)
        return response(content=f"DREAM-IMPORT-COMPLETE:{import_id}")

    second = await run_dream(loop_for(store, confirmed), commit_prefix="dream: manual run")

    assert second.error is None
    assert second.had_input is True
    assert store.imports.list_entries()[0]["id"] == imported["id"]
    assert store.imports.list_entries()[0]["status"] == "archived"
    assert len(prompts) == 2
    assert store.get_last_dream_cursor() == 0


@pytest.mark.asyncio
async def test_import_uses_a_nonpersisting_session_policy(tmp_path):
    store = MemoryStore(tmp_path)
    import_markdown(store, "temporary.md", "# Temporary\nDo not persist the prompt")
    policies = []

    async def process(prompt, **kwargs):
        policies.append(kwargs.get("_session_policy"))
        import_id = import_id_from_prompt(prompt)
        return response(content=f"DREAM-IMPORT-COMPLETE:{import_id}")

    result = await run_dream(loop_for(store, process), commit_prefix="dream: manual run")

    assert result.error is None
    assert len(policies) == 1
    assert policies[0] is not None
    assert policies[0].persist is False
    assert policies[0].log_content is False


@pytest.mark.asyncio
async def test_import_turn_does_not_persist_a_dream_session(tmp_path):
    from nanobot.agent.loop import AgentLoop
    from nanobot.bus.queue import MessageBus
    from nanobot.providers.base import GenerationSettings, LLMResponse

    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    provider.supports_tools = True
    provider.generation = GenerationSettings()
    provider.estimate_prompt_tokens.return_value = (1_000, "test-provider")

    async def chat(**kwargs):
        prompt = "\n".join(str(message.get("content", "")) for message in kwargs["messages"])
        import_id = import_id_from_prompt(prompt)
        return LLMResponse(content=f"DREAM-IMPORT-COMPLETE:{import_id}")

    provider.chat_stream_with_retry = AsyncMock(side_effect=chat)
    loop = AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=tmp_path,
        context_window_tokens=100_000,
    )
    store = loop.context.memory
    try:
        import_markdown(store, "temporary.md", "# Temporary\nDo not persist the prompt")
        result = await run_dream(loop, commit_prefix="dream: manual run")

        assert result.error is None
        assert await loop.sessions.state.list_sessions() == []
    finally:
        await loop.sessions.state.aclose()


@pytest.mark.asyncio
async def test_import_added_during_dream_waits_for_next_run(tmp_path):
    store = MemoryStore(tmp_path)
    first = import_markdown(store, "first.md", "# First\nInitial document")
    calls = 0
    late = {}

    async def process(prompt, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            late["entry"] = import_markdown(store, "late.md", "# Late\nNext document")
        import_id = import_id_from_prompt(prompt)
        return response(content=f"DREAM-IMPORT-COMPLETE:{import_id}")

    loop = loop_for(store, process)
    first_result = await run_dream(loop, commit_prefix="dream: manual run")
    entries = {entry["id"]: entry["status"] for entry in store.imports.list_entries()}

    assert first_result.error is None
    assert entries[first["id"]] == "archived"
    assert entries[late["entry"]["id"]] == "pending"

    second_result = await run_dream(loop, commit_prefix="dream: manual run")
    entries = {entry["id"]: entry["status"] for entry in store.imports.list_entries()}

    assert second_result.error is None
    assert entries[first["id"]] == "archived"
    assert entries[late["entry"]["id"]] == "archived"


@pytest.mark.asyncio
async def test_history_cursor_and_imports_share_one_dream_run_without_advancing_on_import(tmp_path):
    store = MemoryStore(tmp_path)
    store.append_history("Pending chat summary")
    imported = import_markdown(store, "source.md", "# Source\nExternal material")
    calls = []

    async def process(prompt, **kwargs):
        if "## Conversation History" in prompt:
            calls.append("history")
            return response()
        calls.append("import")
        import_id = import_id_from_prompt(prompt)
        return response(content=f"DREAM-IMPORT-COMPLETE:{import_id}")

    result = await run_dream(loop_for(store, process), commit_prefix="dream: manual run")

    assert result.error is None
    assert calls == ["history", "import"]
    assert store.get_last_dream_cursor() == 1
    assert store.imports.list_entries()[0]["id"] == imported["id"]
    assert store.imports.list_entries()[0]["status"] == "archived"


@pytest.mark.asyncio
async def test_history_context_overflow_does_not_block_imports_or_advance_cursor(tmp_path):
    store = MemoryStore(tmp_path)
    store.append_history("Pending chat summary")
    imported = import_markdown(store, "source.md", "# Source\nUsable external material")
    calls = []

    async def process(prompt, **kwargs):
        if "## Conversation History" in prompt:
            calls.append("history")
            raise ContextWindowExceededError(
                session_key=kwargs["session_key"],
                estimated_tokens=20_000,
                input_budget=8_000,
                source="test",
            )
        calls.append("import")
        import_id = import_id_from_prompt(prompt)
        return response(content=f"DREAM-IMPORT-COMPLETE:{import_id}")

    result = await run_dream(loop_for(store, process), commit_prefix="dream: manual run")

    entry = store.imports.list_entries()[0]
    assert calls == ["history", "import"]
    assert result.error
    assert result.had_input is True
    assert store.get_last_dream_cursor() == 0
    assert entry["id"] == imported["id"]
    assert entry["status"] == "archived"


@pytest.mark.asyncio
async def test_import_that_does_not_fit_model_context_stays_pending(tmp_path):
    store = MemoryStore(tmp_path)
    imported = import_markdown(store, "large.md", "# Large\nFull source")

    async def process(prompt, **kwargs):
        raise ContextWindowExceededError(
            session_key=kwargs["session_key"],
            estimated_tokens=20_000,
            input_budget=8_000,
            source="test",
        )

    result = await run_dream(loop_for(store, process), commit_prefix="dream: manual run")

    entry = store.imports.list_entries()[0]
    assert result.error and "1 imported file failed" in result.error
    assert entry["id"] == imported["id"]
    assert entry["status"] == "failed"
    assert entry["error"] == "Imported file exceeds the Dream model context"
