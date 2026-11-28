import hashlib
import json
from zipfile import ZipFile

import pytest

from scripts.native_acceptance_input import acceptance_input

COMMIT = "a" * 40
TAG = "win_amd64"


def candidate(tmp_path, version="0.3.6.dev42"):
    wheel = tmp_path / f"nanobot_ai-{version}-py3-none-win_amd64.whl"
    with ZipFile(wheel, "w") as archive:
        archive.writestr("nanobot/_build_commit.txt", COMMIT + "\n")
    manifest = {"candidate_commit": COMMIT, "version": version, "artifacts": [{
        "filename": wheel.name, "size": wheel.stat().st_size,
        "sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
    }]}
    payload = json.dumps(manifest).encode()
    (tmp_path / "release-manifest.json").write_bytes(payload)
    return wheel, hashlib.sha256(payload).hexdigest()


def test_select_exact_candidate_wheel(tmp_path):
    wheel, digest = candidate(tmp_path)
    result = acceptance_input(tmp_path, tag=TAG, commit=COMMIT, manifest_sha256=digest)
    assert result["wheel"] == str(wheel)
    assert result["version"] == "0.3.6.dev42"
    assert result["manifest_sha256"] == digest


def test_reject_replaced_wheel(tmp_path):
    wheel, digest = candidate(tmp_path)
    wheel.write_bytes(wheel.read_bytes() + b"changed")
    with pytest.raises(ValueError, match="wheel differs"):
        acceptance_input(tmp_path, tag=TAG, commit=COMMIT, manifest_sha256=digest)


def test_reject_replaced_manifest(tmp_path):
    _, digest = candidate(tmp_path)
    path = tmp_path / "release-manifest.json"
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(ValueError, match="manifest differs"):
        acceptance_input(tmp_path, tag=TAG, commit=COMMIT, manifest_sha256=digest)


def test_reject_wrong_candidate_commit(tmp_path):
    _, digest = candidate(tmp_path)
    with pytest.raises(ValueError, match="Candidate commit differs"):
        acceptance_input(tmp_path, tag=TAG, commit="b" * 40, manifest_sha256=digest)


def test_pr_package_must_embed_checked_out_commit(tmp_path):
    wheel, _ = candidate(tmp_path)
    result = acceptance_input(tmp_path, tag=TAG, commit=COMMIT, version="0.3.6.dev42")
    assert result["wheel_sha256"] == hashlib.sha256(wheel.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="Wheel build commit differs"):
        acceptance_input(tmp_path, tag=TAG, commit="b" * 40, version="0.3.6.dev42")


def test_select_exact_fork_wheel_with_local_version(tmp_path):
    wheel, _ = candidate(tmp_path, "0.4.2+xm.1")
    result = acceptance_input(tmp_path, tag=TAG, commit=COMMIT, version="0.4.2+xm.1")
    assert result["wheel"] == str(wheel)
    assert result["version"] == "0.4.2+xm.1"
    assert result["commit"] == COMMIT
    assert result["wheel_sha256"] == hashlib.sha256(wheel.read_bytes()).hexdigest()


def test_candidate_manifest_rejects_local_version(tmp_path):
    _, digest = candidate(tmp_path, "0.4.2+xm.1")
    with pytest.raises(ValueError, match="Invalid acceptance version"):
        acceptance_input(tmp_path, tag=TAG, commit=COMMIT, manifest_sha256=digest)
