# Updating nanobot-XMFork

nanobot-XMFork checks this Fork's GitHub tags for newer versions. The check only reports the tag; it does not download or install anything.

This Fork does not publish PyPI packages, signed release installers, or prebuilt Docker images. The WebUI **Update & restart** action, `nanobot update`, `uv tool upgrade nanobot-ai`, `pipx upgrade nanobot-ai`, and `pip install --upgrade nanobot-ai` target upstream releases and cannot install the version shown in the update notice. `nanobot update --dev` only fast-forwards a branch to its configured upstream; it does not select the displayed tag and fails from a detached checkout.

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

On Windows, `start-webui.cmd` refreshes the editable source installation and its launcher-local environment when you start it. Restart the gateway through the same local service or process you used before the update.

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

After restarting, confirm the running version under **Settings → About** or with the `python -m nanobot --version` command above.

To roll back, check out the previous Fork tag, run `python -m pip install -e .` for a source checkout, or rebuild and recreate the Compose service, then verify the version again.

Official-installer, `pipx`, `uv` tool, and PyPI virtual-environment installations have no matching Fork package. Use a Fork source checkout or Docker Compose deployment to receive these tag updates; package-manager upgrades continue to install upstream releases.
