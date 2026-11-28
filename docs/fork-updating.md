# Updating nanobot-XMFork

nanobot-XMFork checks this Fork's GitHub tags for newer versions. Checking does not install anything. In **Settings → About**, an editable source installation with its own Python environment and Git can offer **Update & restart**. That button fetches the exact displayed Fork tag, checks its commit and package version, and asks the original launcher to stop the idle gateway, install the update, and restart. The notice does not advance to a newer tag if one is published while the page is open. The page reloads after the reconnected gateway reports the expected version.

This Fork does not publish PyPI packages, signed release installers, or prebuilt Docker images. `nanobot update`, `uv tool upgrade nanobot-ai`, `pipx upgrade nanobot-ai`, and `pip install --upgrade nanobot-ai` target upstream releases and cannot install the version shown in the Fork update notice. `nanobot update --dev` only fast-forwards a branch to its configured upstream; it does not select the displayed tag and fails from a detached checkout.

The one-click source update works with `nanobot webui` (including the Windows launcher), or a gateway started directly with `nanobot gateway`. Finish running tasks, exit any native TUI using the same Python environment, and commit or stash local changes first. The updater leaves the checkout detached at the selected commit and does not move your branch. If rebuilding the WebUI or refreshing the editable package fails, it tries to restore the previous checkout and installation before relaunching. Remote WebUI users need the gateway's existing remote package-install permission. For containers and installations managed by another package manager, use the deployment-specific instructions below.

## Before You Update

1. Stop the gateway that is running the current version.
2. Back up your config, workspace, sessions, and any custom files.
3. Check `git status` before switching tags. Commit or stash local changes first; do not discard them with a hard reset.

For a source checkout, the default data directory is `~/.nanobot`. The Windows launcher used by this Fork keeps its config, workspace, virtual environment, and caches under `userdatas/` in the checkout.

For Docker Compose, back up the host directory mounted at `/home/nanobot/.nanobot`. The default is `~/.nanobot`.

## Update a Source Checkout

Use the exact Fork tag shown by the update notice. For example, a notice for `v0.4.2-xm.1` corresponds to Python package version `0.4.2+xm.1`.

```bash
git fetch origin --tags
git tag --list 'v*-xm.*' --sort=-version:refname
git switch --detach <fork-tag>
```

If the checkout uses its own Python virtual environment, refresh the editable installation from the new tag:

```bash
python -m pip install -e .
python -m nanobot --version
```

If the gateway runs as a background service and serves the WebUI directly, rebuild the production frontend from the target tag before restarting it. An editable Python install does not refresh the existing WebUI bundle, and a non-interactive gateway start only warns about stale assets:

```bash
cd webui
bun install --frozen-lockfile
bun run build
cd ..
```

The build writes the target tag's frontend to `nanobot/web/dist/`. Rebuild it before every update that may change WebUI source, then restart the gateway through the same local service or process you used before the update. On Windows, an interactive `start-webui.cmd` launch offers to rebuild stale assets in its launcher-local environment.

## Update Docker Compose

Docker Compose builds this Fork from the checked-out source. Do not use `docker compose pull`: this Fork does not publish a prebuilt image for that command to update.

```bash
git fetch origin --tags
git switch --detach <fork-tag>
docker compose build --pull
docker compose up -d --force-recreate
docker compose exec nanobot-gateway python -m nanobot --version
```

If your deployment uses the `bwrap` override, keep both Compose files in the build and recreate commands:

```bash
docker compose -f docker-compose.yml -f docker-compose.bwrap.yml build --pull
docker compose -f docker-compose.yml -f docker-compose.bwrap.yml up -d --force-recreate
```

Recreating the service does not remove the bind-mounted data directory.

## Verify and Roll Back

After restarting, confirm the Python version under **Settings → About** or with the `python -m nanobot --version` command above. Reload the browser and check that the WebUI still loads correctly after any frontend rebuild.

To roll back, check out the previous Fork tag. For a source checkout, run `python -m pip install -e .`, and rebuild `webui/` with the same `bun install --frozen-lockfile` and `bun run build` commands before restarting the gateway. For Docker Compose, rebuild and recreate the service. In both cases, verify the version and reload the WebUI again.

Official-installer, `pipx`, `uv` tool, and PyPI virtual-environment installations have no matching Fork package. Use a Fork source checkout or Docker Compose deployment to receive these tag updates; package-manager upgrades continue to install upstream releases.
