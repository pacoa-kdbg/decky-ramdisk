"""Tests for state + operations. Runnable with:

    python3 -m unittest discover -s tests -v

These do not require root and do not touch /run. The OperationRunner is
faked to simulate mount/umount/copy in temp directories.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

# Make the plugin's py_modules importable when running from the repo root.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "py_modules"))

from ramdisk_plugin import operations as ops  # noqa: E402
from ramdisk_plugin.models import ActiveMove, SteamGame  # noqa: E402
from ramdisk_plugin.state import MoveStateStore  # noqa: E402


# --- fake runner ----------------------------------------------------------

class FakeRunner:
    """Simulates mount/umount/copy on temp directories.

    Tracks a virtual mount table so ``is_mountpoint`` can be answered without
    touching the real kernel. ``copytree`` actually copies into the fake
    tmpfs directory so verification (count_and_size) compares real files.
    The original install path is replaced with a directory whose contents
    are the overlay's merged view, so reads through the merged path work too.
    """

    def __init__(self, tmpfs_root: Path) -> None:
        self.tmpfs_root = tmpfs_root
        self.mounts: dict[str, dict] = {}
        self.run_calls: list[list[str]] = []
        self.chowns: list[tuple[str, int, int]] = []
        self.rsyncs: list[tuple[str, str]] = []

    # OperationRunner protocol ----------------------------------------
    def run(self, command: list[str]) -> None:
        self.run_calls.append(command)
        if command[:1] == ["mount"] and "-t" in command:
            mtype = command[command.index("-t") + 1]
            mountpoint = command[-1]
            self.mounts[mountpoint] = {"type": mtype, "args": command}
            Path(mountpoint).mkdir(parents=True, exist_ok=True)
            return
        if command[:1] == ["umount"]:
            target = command[-1]
            self.mounts.pop(target, None)
            return

    def is_mountpoint(self, path: Path) -> bool:
        return str(path) in self.mounts

    def copytree(self, src: Path, dst: Path) -> None:
        shutil.copytree(src, dst, symlinks=True)

    def rsync(self, src: Path, dst: Path) -> None:
        self.rsyncs.append((str(src), str(dst)))
        # Approximate `rsync -aHAX --delete`: dst becomes contents of src.
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst, symlinks=True)

    def chown_recursive(self, path: Path, uid: int, gid: int) -> None:
        self.chowns.append((str(path), uid, gid))

    def mkdir(self, path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)

    def remove_tree(self, path: Path) -> None:
        if path.exists():
            shutil.rmtree(path)

    def stat_uid_gid(self, path: Path) -> tuple[int, int]:
        st = path.stat()
        return (st.st_uid, st.st_gid)

    def count_and_size(self, path: Path) -> tuple[int, int]:
        return ops.SubprocessRunner().count_and_size(path)


def _make_steam_install(root: Path, dirname: str = "TestGame") -> SteamGame:
    install_dir = root / "steamapps" / "common" / dirname
    install_dir.mkdir(parents=True, exist_ok=True)
    (install_dir / "game.exe").write_bytes(b"\x00" * 1024)
    (install_dir / "data").mkdir()
    (install_dir / "data" / "level1.pak").write_bytes(b"L1" * 2048)
    return SteamGame(
        appid="12345",
        name="Test Game",
        install_dir=str(install_dir),
        library_path=str(root),
        size_on_disk=1024 + 2 * 2048,
    )


def _stub_overlay_setup(runner: FakeRunner, source: Path):
    """When operations.stage_game asks the runner to mount overlay over the
    source path, our FakeRunner does not actually copy lower into source. So
    we monkeypatch by overriding `run` to do that side-effect for overlay
    mounts only.
    """
    original_run = runner.run

    def patched_run(command: list[str]) -> None:
        original_run(command)
        if command[:1] == ["mount"] and "-t" in command:
            mtype = command[command.index("-t") + 1]
            if mtype == "overlay":
                # Find the lowerdir and make the source mirror it.
                opts = command[command.index("-o") + 1]
                opts_map = dict(part.split("=", 1) for part in opts.split(","))
                lowerdir = Path(opts_map["lowerdir"])
                # Empty the source dir then copy from lower so reads through
                # the "overlay" mountpoint show the seeded contents.
                if source.exists():
                    shutil.rmtree(source)
                shutil.copytree(lowerdir, source, symlinks=True)
        if command[:1] == ["umount"]:
            target = Path(command[-1])
            if target == source:
                # Simulate overlay unmount: lower contents stay on tmpfs,
                # source becomes empty (the real overlay would now expose
                # the original directory underneath, but in our fake the
                # source directory IS the original, so we leave it alone).
                pass

    runner.run = patched_run  # type: ignore[assignment]


# --- tests ----------------------------------------------------------------

class TestStateStore(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.store = MoveStateStore(self.tmp)

    def _make_move(self, **overrides) -> ActiveMove:
        base = ActiveMove(
            appid="1",
            name="X",
            original_library_path="/lib",
            original_install_dir="/lib/steamapps/common/X",
            ramdisk_library_path="/run/media/decky-ramdisk/SteamLibrary",
            ramdisk_mount_path="/run/media/decky-ramdisk",
            size_on_disk=100,
        )
        return replace(base, **overrides)

    def test_write_is_atomic_and_roundtrips(self) -> None:
        move = self._make_move()
        self.store.write(move)
        # The temp file used by atomic write must not be left behind.
        leftovers = list(Path(self.tmp).glob(".active_move.*.tmp"))
        self.assertEqual(leftovers, [])
        loaded = self.store.read()
        self.assertEqual(loaded, move)

    def test_set_phase_persists(self) -> None:
        self.store.write(self._make_move(phase="staging"))
        self.store.set_phase("active")
        self.assertEqual(self.store.read().phase, "active")

    def test_read_tolerates_unknown_keys(self) -> None:
        # Older or newer schema versions should not crash the loader.
        bad = self._make_move().to_dict()
        bad["future_field"] = "x"
        (Path(self.tmp) / "active_move.json").write_text(json.dumps(bad))
        loaded = self.store.read()
        self.assertEqual(loaded.appid, "1")

    def test_clear_removes_file(self) -> None:
        self.store.write(self._make_move())
        self.store.clear()
        self.assertIsNone(self.store.read())


class TestStage(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.lib = self.tmp / "lib"
        self.tmpfs = self.tmp / "tmpfs"
        self.tmpfs.mkdir()
        self.game = _make_steam_install(self.lib)
        self.store = MoveStateStore(str(self.tmp / "settings"))
        self.runner = FakeRunner(self.tmpfs)
        # Redirect RAMDISK_BASE to our tempdir for the test.
        self._patches = [
            patch.object(ops, "RAMDISK_BASE", self.tmpfs),
            patch.object(ops, "RAMDISK_LIBRARY", self.tmpfs / "SteamLibrary"),
        ]
        for p in self._patches:
            p.start()
            self.addCleanup(p.stop)

    def test_dry_run_produces_plan(self) -> None:
        result = ops.stage_game(self.game, self.store, dry_run=True, runner=self.runner)
        self.assertTrue(result.ok)
        self.assertEqual(result.kind, "move")
        self.assertTrue(result.details["dry_run"])
        # Dry run must not touch state or call run().
        self.assertIsNone(self.store.read())
        self.assertEqual(self.runner.run_calls, [])

    def test_stage_real_writes_state_and_mounts(self) -> None:
        _stub_overlay_setup(self.runner, Path(self.game.install_dir))
        result = ops.stage_game(self.game, self.store, dry_run=False, runner=self.runner)
        self.assertTrue(result.ok, result.message)
        # State persisted and active.
        state = self.store.read()
        self.assertIsNotNone(state)
        self.assertEqual(state.phase, "active")
        self.assertEqual(state.appid, self.game.appid)
        # Both tmpfs and overlay mounts should be present.
        self.assertIn(str(self.tmpfs), self.runner.mounts)
        self.assertIn(self.game.install_dir, self.runner.mounts)

    def test_stage_refuses_when_state_exists(self) -> None:
        # Pre-existing state should block a new stage.
        self.store.write(ActiveMove(
            appid="other", name="Other", original_library_path="/x",
            original_install_dir="/x/y", ramdisk_library_path="/run/x",
            ramdisk_mount_path="/run/x", size_on_disk=1, phase="active",
        ))
        result = ops.stage_game(self.game, self.store, dry_run=False, runner=self.runner)
        self.assertFalse(result.ok)
        self.assertEqual(result.kind, "plan")

    def test_stage_refuses_symlinked_source(self) -> None:
        source = Path(self.game.install_dir)
        shutil.rmtree(source)
        source.symlink_to(self.tmp)
        result = ops.stage_game(self.game, self.store, dry_run=False, runner=self.runner)
        self.assertFalse(result.ok)
        self.assertIn("symlink", result.message)


class TestRevert(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.lib = self.tmp / "lib"
        self.tmpfs = self.tmp / "tmpfs"
        self.tmpfs.mkdir()
        self.game = _make_steam_install(self.lib)
        self.store = MoveStateStore(str(self.tmp / "settings"))
        self.runner = FakeRunner(self.tmpfs)
        self._patches = [
            patch.object(ops, "RAMDISK_BASE", self.tmpfs),
            patch.object(ops, "RAMDISK_LIBRARY", self.tmpfs / "SteamLibrary"),
        ]
        for p in self._patches:
            p.start()
            self.addCleanup(p.stop)
        _stub_overlay_setup(self.runner, Path(self.game.install_dir))
        ok = ops.stage_game(self.game, self.store, dry_run=False, runner=self.runner)
        self.assertTrue(ok.ok, ok.message)

    def test_dry_run_discard(self) -> None:
        result = ops.revert_active_move(
            self.store, dry_run=True, commit_changes=False, runner=self.runner
        )
        self.assertTrue(result.ok)
        # State must still be there after a dry-run.
        self.assertIsNotNone(self.store.read())

    def test_dry_run_commit(self) -> None:
        result = ops.revert_active_move(
            self.store, dry_run=True, commit_changes=True, runner=self.runner
        )
        self.assertTrue(result.ok)
        self.assertIn("rsync", result.message)
        self.assertIsNotNone(self.store.read())

    def test_real_discard_unmounts_and_clears_state(self) -> None:
        result = ops.revert_active_move(
            self.store, dry_run=False, commit_changes=False, runner=self.runner
        )
        self.assertTrue(result.ok, result.message)
        self.assertIsNone(self.store.read())
        # Both mounts gone.
        self.assertNotIn(str(self.tmpfs), self.runner.mounts)
        self.assertNotIn(self.game.install_dir, self.runner.mounts)
        # No rsync on discard.
        self.assertEqual(self.runner.rsyncs, [])

    def test_real_commit_runs_rsync(self) -> None:
        result = ops.revert_active_move(
            self.store, dry_run=False, commit_changes=True, runner=self.runner
        )
        self.assertTrue(result.ok, result.message)
        self.assertIsNone(self.store.read())
        self.assertEqual(len(self.runner.rsyncs), 1)


class TestRecover(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.store = MoveStateStore(str(self.tmp / "settings"))
        self.runner = FakeRunner(self.tmp)

    def _seed(self, phase="active") -> ActiveMove:
        move = ActiveMove(
            appid="1", name="Game",
            original_library_path="/lib",
            original_install_dir="/lib/steamapps/common/Game",
            ramdisk_library_path="/run/media/decky-ramdisk/SteamLibrary",
            ramdisk_mount_path="/run/media/decky-ramdisk",
            size_on_disk=10,
            phase=phase,
            merged_dir="/lib/steamapps/common/Game",
        )
        self.store.write(move)
        return move

    def test_no_state_is_noop(self) -> None:
        result = ops.recover(self.store, runner=self.runner)
        self.assertTrue(result.ok)
        self.assertTrue(result.details["healthy"])

    def test_post_reboot_clears_state(self) -> None:
        self._seed("active")
        # No mounts present -> looks like a reboot.
        result = ops.recover(self.store, runner=self.runner)
        self.assertTrue(result.ok)
        self.assertTrue(result.details["cleared_state"])
        self.assertIsNone(self.store.read())

    def test_mid_staging_crash_tears_down(self) -> None:
        move = self._seed("staging")
        # Pretend both mounts are still present from the crash.
        self.runner.mounts[move.ramdisk_mount_path] = {"type": "tmpfs", "args": []}
        self.runner.mounts[move.merged_dir] = {"type": "overlay", "args": []}
        result = ops.recover(self.store, runner=self.runner)
        self.assertTrue(result.ok)
        self.assertTrue(result.details["cleared_state"])
        self.assertIn(move.merged_dir, result.details["forced_unmounts"])
        self.assertIsNone(self.store.read())

    def test_healthy_active_is_reported(self) -> None:
        move = self._seed("active")
        self.runner.mounts[move.ramdisk_mount_path] = {"type": "tmpfs", "args": []}
        self.runner.mounts[move.merged_dir] = {"type": "overlay", "args": []}
        result = ops.recover(self.store, runner=self.runner)
        self.assertTrue(result.ok)
        self.assertFalse(result.details["cleared_state"])
        # State is preserved.
        self.assertIsNotNone(self.store.read())


if __name__ == "__main__":
    unittest.main()
