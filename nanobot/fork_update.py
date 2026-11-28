"""Hand an exact Fork tag back to the CLI that owns this gateway's lifetime."""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
import time
from collections.abc import Callable, Generator
from contextlib import AbstractContextManager, ExitStack, contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from packaging.version import Version

import nanobot
from nanobot.installation_lock import (
    InstallationBusyError,
    installation_access,
    installation_state_dir,
)
from nanobot.repository import ForkTarget
from nanobot.update import (
    ForkPreflightError,
    fork_source_root,
    incomplete_update,
    install_fork_target,
    prepare_fork_target,
)
from nanobot.utils.atomic_file import atomic_write_lines, read_atomic_bytes

if TYPE_CHECKING:
    from nanobot.update_profiles import ProfileReceipt

_LAUNCHER = "NANOBOT_FORK_WEBUI_LAUNCHER"


def _webui_mailbox() -> Path | None:
    key = os.environ.get(_LAUNCHER, "")
    if re.fullmatch(r"[0-9a-f]{32}", key):
        directory = installation_state_dir() / f"webui-{key}"
        if directory.is_dir():
            return directory / "target.json"
    return None


def _report(target: ForkTarget, phase: str) -> None:
    path = installation_state_dir() / "fork-update.json"
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    atomic_write_lines(path, [json.dumps({**asdict(target), "phase": phase})])


def status() -> dict[str, object]:
    try:
        last = json.loads(read_atomic_bytes(installation_state_dir() / "fork-update.json", max_bytes=2048))
    except FileNotFoundError:
        last = None
    return {"runningVersion": nanobot.__version__, "last": last}


@dataclass
class GatewayHandoff:
    profile: ProfileReceipt
    stop: Callable[[], None]
    maintenance: Callable[[], AbstractContextManager[None]]
    gates: ExitStack
    target: ForkTarget | None = None
    preparing: bool = False

    def available(self) -> bool:
        launch = self.profile.launch
        return bool(_owner and launch and (launch.lifetime == "explicit" or _webui_mailbox())
                    and fork_source_root() is not None)

    async def submit(self, target: ForkTarget, revision: int) -> None:
        from nanobot.update_channel import read_update_channel
        from nanobot.update_profiles import inspect_profiles

        if self.target == target:
            return
        if self.preparing or self.target or not self.available():
            raise ForkPreflightError("No Fork update can be started for this gateway.")
        selection = read_update_channel()
        if selection.revision != revision or (selection.channel == "stable" and Version(target.version).is_prerelease):
            raise ForkPreflightError("Update channel changed. Check for updates again.")
        self.preparing = True
        try:
            peers = await asyncio.to_thread(inspect_profiles, self.profile.environment)
            if any(p.state != "stopped" and p.receipt.id != self.profile.id for p in peers):
                raise ForkPreflightError("Stop other gateways sharing this Python installation before updating.")
            await asyncio.to_thread(prepare_fork_target, target)
            if read_update_channel() != selection:
                raise ForkPreflightError("Update channel changed. Check for updates again.")
            with ExitStack() as gate:
                gate.enter_context(self.maintenance())
                assert self.profile.launch is not None and _owner is not None
                if self.profile.launch.lifetime == "on_demand":
                    mailbox = _webui_mailbox()
                    if mailbox is None:
                        raise ForkPreflightError("The WebUI launcher has exited.")
                    _report(target, "installing")
                    atomic_write_lines(mailbox, [json.dumps(asdict(target))])
                else:
                    _report(target, "installing")
                    _owner.target = target
                    _owner.background = self.profile.launch_mode == "background"
                self.target = target
                self.gates.enter_context(gate.pop_all())
            # Let the mutation acknowledgement reach the browser before normal shutdown.
            asyncio.get_running_loop().call_later(0.25, self.stop)
        finally:
            self.preparing = False


active_gateway: GatewayHandoff | None = None


def bind_gateway(handoff: GatewayHandoff) -> None:
    global active_gateway
    active_gateway = handoff

    def clear() -> None:
        global active_gateway
        active_gateway = None

    handoff.gates.callback(clear)


@dataclass
class _Owner:
    target: ForkTarget | None = None
    background: bool = False


_owner: _Owner | None = None


def _finish(target: ForkTarget, background: bool) -> None:
    # A closing webui client can briefly outlive its gateway's state file.
    deadline = time.monotonic() + 12
    try:
        while True:
            try:
                with installation_access(updating=True):
                    install_fork_target(target)
                break
            except InstallationBusyError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.2)
        _report(target, "restarting")
    except Exception as exc:
        _report(target, "failed")
        print(f"Fork update failed ({type(exc).__name__}).", file=sys.stderr)
        if incomplete_update():
            raise SystemExit("The previous installation could not be restored; repair it before restarting.") from None
    args = list(sys.argv[1:])
    if background:
        # Managed children run --foreground. Recreate the registered background instance instead.
        position = args.index("gateway")
        args[args.index("--foreground", position + 1)] = "--background"
    argv = [sys.executable, "-m", "nanobot", *args]
    if sys.platform == "win32":
        argv = [subprocess.list2cmdline([arg]) for arg in argv]
    os.execv(sys.executable, argv)


@contextmanager
def launcher(command: str) -> Generator[None]:
    """Only the original CLI resumes after every shared installation lease is released."""
    global _owner
    owner = _owner = _Owner()
    directory = None
    previous = os.environ.get(_LAUNCHER)
    normal_exit = None
    try:
        if command == "webui":
            key = uuid4().hex
            directory = installation_state_dir() / f"webui-{key}"
            directory.mkdir(mode=0o700, parents=True)
            os.environ[_LAUNCHER] = key
        try:
            yield
        except SystemExit as exc:
            if exc.code not in (None, 0):
                raise
            normal_exit = exc
        if directory is not None and (directory / "target.json").exists():
            owner.target = ForkTarget(**json.loads((directory / "target.json").read_text(encoding="utf-8")))
    finally:
        _owner = None
        if directory is not None:
            (directory / "target.json").unlink(missing_ok=True)
            directory.rmdir()
            if previous is None:
                os.environ.pop(_LAUNCHER, None)
            else:
                os.environ[_LAUNCHER] = previous
    if owner.target:
        _finish(owner.target, owner.background)
    elif normal_exit is not None:
        raise normal_exit
