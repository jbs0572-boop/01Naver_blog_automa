from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tools import runner_state
from tools.contract_types import ContractError


def test_atomic_state_write_rejects_parent_swap_and_restores_previous_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent = tmp_path / "state"
    parent.mkdir()
    target = parent / "run.json"
    _ = target.write_bytes(b"previous state")
    moved_parent = tmp_path / "moved-state"
    original_replace = os.replace
    swapped = False

    def swap_parent_after_replace(
        source: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        destination: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
    ) -> None:
        nonlocal swapped
        original_replace(
            source,
            destination,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
        )
        if not swapped:
            swapped = True
            _ = parent.rename(moved_parent)
            _ = parent.mkdir()
            _ = (parent / "attacker-marker").write_bytes(b"leave intact")

    monkeypatch.setattr(os, "replace", swap_parent_after_replace)

    with pytest.raises(ContractError, match="path changed during state write"):
        runner_state.atomic_write_json(target, {"status": "passed"})

    assert (moved_parent / target.name).read_bytes() == b"previous state"
    assert not target.exists()
    assert (parent / "attacker-marker").read_bytes() == b"leave intact"


def test_atomic_state_write_rejects_symlinked_parent(tmp_path: Path) -> None:
    actual = tmp_path / "actual"
    actual.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(actual, target_is_directory=True)

    with pytest.raises(ContractError, match="path is unsafe"):
        runner_state.atomic_write_json(alias / "run.json", {"status": "passed"})

    assert not (actual / "run.json").exists()


def test_atomic_state_write_keeps_existing_json_contract(tmp_path: Path) -> None:
    target = tmp_path / "new" / "run.json"

    runner_state.atomic_write_json(target, {"status": "passed", "label": "검증"})

    assert json.loads(target.read_text(encoding="utf-8")) == {
        "label": "검증",
        "status": "passed",
    }
