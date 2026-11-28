"""Shared manual and scheduled Dream execution over a bounded history snapshot."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from loguru import logger

from nanobot.agent.context_governance import ContextWindowExceededError
from nanobot.agent.memory import MemoryStore
from nanobot.session.manager import SessionPolicy

if TYPE_CHECKING:
    from nanobot.agent.loop import AgentLoop


@dataclass
class DreamResult:
    had_input: bool = False
    changed: bool = False
    error: str | None = None
    commit_sha: str | None = None


async def run_dream(loop: AgentLoop, *, commit_prefix: str) -> DreamResult:
    """Run one Dream over pending chat summaries and imported documents."""
    store = loop.context.memory
    result = DreamResult()
    confirmed_imports: list[str] = []
    import_failures = 0

    async def silent(*_args: Any, **_kwargs: Any) -> None:
        pass

    def append_error(message: str) -> None:
        result.error = f"{result.error}; {message}" if result.error else message

    def record_import_failure(import_id: str, message: str) -> None:
        nonlocal import_failures
        import_failures += 1
        logger.warning("Dream import {} failed: {}", import_id, message)
        try:
            store.imports.mark_failed(import_id, message)
        except Exception:
            logger.exception("Could not save Dream import failure status for {}", import_id)

    async with store.dream_lock:
        runtime = None
        runtime_resolved = False

        def get_runtime() -> Any:
            nonlocal runtime, runtime_resolved
            if not runtime_resolved:
                runtime = loop.dream_runtime()
                runtime_resolved = True
            return runtime

        try:
            import_ids = store.imports.retryable_ids()
            try:
                history = store.build_dream_prompt()
                if history is not None:
                    result.had_input = True
                    prompt, last_cursor = history
                    response = await loop.process_direct(
                        prompt,
                        session_key=MemoryStore.dream_session_key(),
                        ephemeral=True,
                        tools=store.build_dream_tools(),
                        on_progress=silent,
                        runtime=get_runtime(),
                    )
                    if MemoryStore.dream_run_completed(response):
                        store.set_last_dream_cursor(last_cursor)
                    else:
                        result.error = MemoryStore.dream_incompletion_reason(response)
            except Exception as exc:
                result.error = str(exc)
                logger.exception("Dream history range failed")

            for import_id in import_ids:
                result.had_input = True
                message = "Dream did not confirm the complete imported file"
                try:
                    entry, content = store.imports.read_pending(import_id)
                    response = await loop.process_direct(
                        store.build_import_prompt(
                            import_id=import_id,
                            name=str(entry["name"]),
                            content=content,
                        ),
                        session_key=MemoryStore.dream_session_key(),
                        ephemeral=True,
                        tools=store.build_dream_tools(),
                        on_progress=silent,
                        runtime=get_runtime(),
                        _session_policy=SessionPolicy(
                            persist=False,
                            log_content=False,
                        ),
                    )
                    if not MemoryStore.dream_run_completed(response):
                        message = MemoryStore.dream_incompletion_reason(response)
                        record_import_failure(import_id, message)
                        continue
                    if not MemoryStore.dream_import_completed(response, import_id):
                        record_import_failure(import_id, message)
                        continue
                    confirmed_imports.append(import_id)
                except ContextWindowExceededError:
                    record_import_failure(
                        import_id,
                        "Imported file exceeds the Dream model context",
                    )
                except Exception:
                    logger.exception("Dream import processing failed for {}", import_id)
                    record_import_failure(import_id, "Dream run failed for the imported file")
        except Exception as exc:
            result.error = str(exc)
            logger.exception("Dream failed")

        finalization_failed = False
        try:
            diff = store.dream_content_diff()
            result.changed = bool(diff)
            if diff and store.git.is_initialized():
                result.commit_sha = store.git.auto_commit(
                    MemoryStore.build_dream_commit_message(commit_prefix, diff),
                )
            store.compact_history()
        except Exception as exc:
            finalization_failed = True
            failure = f"Memory finalization failed: {exc}"
            append_error(failure)
            logger.exception("Dream memory finalization failed")

        if finalization_failed:
            for import_id in confirmed_imports:
                record_import_failure(import_id, "Dream memory finalization failed")
            confirmed_imports.clear()
        else:
            for import_id in confirmed_imports:
                try:
                    store.imports.archive(import_id)
                except Exception:
                    logger.exception("Could not archive Dream import {}", import_id)
                    record_import_failure(import_id, "Imported file could not be archived")

        if import_failures:
            append_error(
                f"{import_failures} imported file"
                f"{'s' if import_failures != 1 else ''} failed",
            )

        try:
            await loop.sessions.state.prune_dream_sessions()
        except Exception:
            logger.exception("Could not prune Dream sessions")
    return result
