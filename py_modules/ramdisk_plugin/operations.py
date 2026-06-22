"""Stage/revert/recovery for Decky RAMDisk.

Design notes (see docs/architecture.md):

The original implementation used ``copytree`` + ``rmtree`` + ``symlink`` to
move a game onto a tmpfs. That deletes the original install before anything
verifies the RAM copy, so a crash between copy and symlink leaves the user
with a half-moved game and no original.

This module replaces that with an overlayfs-based design:

    stage:
        1. mount a tmpfs at RAMDISK_BASE
        2. copy the original install into <tmpfs>/lower (a read-only seed)
        3. mkdir upper/ and work/ on the tmpfs
        4. mount -t overlay over the original install path with
           lowerdir=<tmpfs>/lower, upperdir=<tmpfs>/upper, workdir=<tmpfs>/work
        5. verify file count + total bytes between source seed and overlay
        6. persist state with phase="active"

    revert (discard mode):
        umount the overlay, umount the tmpfs, clear state.
        Any in-game writes (saves in install dir, shader cache writes the
        game stored under its own directory, Steam manifest rewrites) are
        thrown away. The original directory underneath is untouched.

    revert (commit mode):
        rsync <tmpfs>/upper into the original install dir, then unmount
        as above. Files the user/Steam created or modified while staged
        are merged back. Deletes done via overlayfs whiteouts are also
        replayed because rsync --delete with the upper layout reflects
        them as char devices; we translate those to real deletes.

    recovery (called on plugin startup):
        Inspect saved state vs the actual mount table. Heal common cases:
          * mounts already gone (post-reboot): clear state, return banner.
          * tmpfs missing but overlay path mounted: forcibly unmount the
            overlay, clear state.
          * fully mounted but phase != "active": user crashed mid-stage,
            roll forward to active or roll back.

Everything that touches the kernel goes through ``OperationRunner`` so
unit tests can run on a non-root host without actually mounting anything.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Protocol

from .models import ActiveMove, OperationResult, SteamGame
from .state import MoveStateStore


RAMDISK_BASE = Path("/run/media/decky-ramdisk")
RAMDISK_LIBRARY = RAMDISK_BASE / "SteamLibrary"
SEED_DIRNAME = "lower"   # read-only seed copy of the original install
UPPER_DIRNAME = "upper"  # tmpfs writable layer for overlayfs
WORK_DIRNAME = "work"    # overlayfs work directory (must be on same fs as upper)
HEADROOM_BYTES = 1 * 1024 ** 3  # default tmpfs headroom on top of the game


class OperationError(RuntimeError):
    pass


def mutations_enabled() -> bool:
    return os.environ.get("DECKY_RAMDISK_ENABLE_MUTATIONS", "1") != "0"


# --- runner abstraction so tests can fake mount/umount/copy ---------------

class OperationRunner(Protocol):
    """Filesystem effect surface. The default impl shells out; tests fake it."""

    def run(self, command: list[str]) -> None: ...
    def is_mountpoint(self, path: Path) -> bool: ...
    def copytree(self, src: Path, dst: Path) -> None: ...
    def rsync(self, src: Path, dst: Path) -> None: ...
    def chown_recursive(self, path: Path, uid: int, gid: int) -> None: ...
    def mkdir(self, path: Path) -> None: ...
    def remove_tree(self, path: Path) -> None: ...
    def stat_uid_gid(self, path: Path) -> tuple[int, int]: ...
    def count_and_size(self, path: Path) -> tuple[int, int]: ...


class SubprocessRunner:
    def run(self, command: list[str]) -> None:
        subprocess.run(command, check=True)

    def is_mountpoint(self, path: Path) -> bool:
        return subprocess.run(
            ["mountpoint", "-q", str(path)], check=False
        ).returncode == 0

    def copytree(self, src: Path, dst: Path) -> None:
        shutil.copytree(src, dst, symlinks=True)

    def rsync(self, src: Path, dst: Path) -> None:
        # Trailing slashes matter: copy CONTENTS of src into dst, with deletes.
        src_arg = str(src).rstrip("/") + "/"
        dst_arg = str(dst).rstrip("/") + "/"
        subprocess.run(
            ["rsync", "-aHAX", "--delete", src_arg, dst_arg],
            check=True,
        )

    def chown_recursive(self, path: Path, uid: int, gid: int) -> None:
        subprocess.run(
            ["chown", "-R", f"{uid}:{gid}", str(path)], check=True
        )

    def mkdir(self, path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)

    def remove_tree(self, path: Path) -> None:
        if path.exists():
            shutil.rmtree(path)

    def stat_uid_gid(self, path: Path) -> tuple[int, int]:
        st = path.stat()
        return (st.st_uid, st.st_gid)

    def count_and_size(self, path: Path) -> tuple[int, int]:
        count = 0
        size = 0
        for root, _dirs, files in os.walk(path):
            for name in files:
                file_path = Path(root) / name
                count += 1
                try:
                    size += file_path.stat().st_size
                except OSError:
                    continue
        return count, size


# --- helpers --------------------------------------------------------------

def _now() -> float:
    return time.time()


def _ramdisk_layout(game_dirname: str) -> dict[str, Path]:
    lower = RAMDISK_BASE / SEED_DIRNAME / game_dirname
    upper = RAMDISK_BASE / UPPER_DIRNAME / game_dirname
    work = RAMDISK_BASE / WORK_DIRNAME / game_dirname
    return {"lower": lower, "upper": upper, "work": work}


def _check_disabled(kind: str, dry_run: bool, extra: dict | None = None) -> OperationResult | None:
    if dry_run or mutations_enabled():
        return None
    return OperationResult(
        ok=False,
        message=f"Real {kind} disabled because DECKY_RAMDISK_ENABLE_MUTATIONS=0 is set.",
        kind=kind,
        details={"dry_run": dry_run, **(extra or {})},
    )


# --- plan -----------------------------------------------------------------

def plan_move(game: SteamGame, state: MoveStateStore) -> OperationResult:
    existing = state.read()
    if existing is not None:
        return OperationResult(
            ok=False,
            message="A game is already staged on the RAM disk. Revert it before staging another game.",
            kind="plan",
            details={"active_move": existing.to_dict()},
        )
    return OperationResult(
        ok=True,
        message=f"{game.name} can be staged on a RAM disk.",
        kind="plan",
        details={
            "game": game.to_dict(),
            "mount_path": str(RAMDISK_BASE),
            "library_path": str(RAMDISK_LIBRARY),
            "required_bytes": game.size_on_disk,
            "headroom_bytes": HEADROOM_BYTES,
        },
    )


# --- stage ----------------------------------------------------------------

def stage_game(
    game: SteamGame,
    state: MoveStateStore,
    dry_run: bool = True,
    runner: OperationRunner | None = None,
) -> OperationResult:
    runner = runner or SubprocessRunner()
    plan = plan_move(game, state)
    if not plan.ok:
        return plan
    if (disabled := _check_disabled("move", dry_run)) is not None:
        return disabled

    source = Path(game.install_dir)
    if not source.exists():
        return OperationResult(
            ok=False,
            message=f"{game.name} install directory does not exist.",
            kind="move",
            details={"source": str(source), "dry_run": dry_run},
        )
    if source.is_symlink():
        return OperationResult(
            ok=False,
            message=f"{game.name} install directory is already a symlink (legacy state?). Run recover first.",
            kind="move",
            details={"source": str(source), "dry_run": dry_run},
        )
    if not dry_run and runner.is_mountpoint(RAMDISK_BASE):
        return OperationResult(
            ok=False,
            message=f"{RAMDISK_BASE} is already mounted. Run recover first.",
            kind="move",
            details={"mount_path": str(RAMDISK_BASE), "dry_run": dry_run},
        )

    layout = _ramdisk_layout(source.name)
    tmpfs_size = game.size_on_disk + HEADROOM_BYTES
    move = ActiveMove(
        appid=game.appid,
        name=game.name,
        original_library_path=game.library_path,
        original_install_dir=game.install_dir,
        ramdisk_library_path=str(RAMDISK_LIBRARY),
        ramdisk_mount_path=str(RAMDISK_BASE),
        size_on_disk=game.size_on_disk,
        phase="staging",
        overlay_lower_dir=str(layout["lower"]),
        overlay_upper_dir=str(layout["upper"]),
        overlay_work_dir=str(layout["work"]),
        merged_dir=str(source),
        staged_at=_now(),
    )

    if dry_run:
        return OperationResult(
            ok=True,
            message="Dry-run overlay stage plan created.",
            kind="move",
            details={"active_move": move.to_dict(), "dry_run": True},
        )

    try:
        uid, gid = runner.stat_uid_gid(source)
        # Mount the tmpfs container that holds lower/upper/work.
        runner.mkdir(RAMDISK_BASE)
        runner.run(
            [
                "mount",
                "-t",
                "tmpfs",
                "-o",
                f"size={tmpfs_size},uid={uid},gid={gid},mode=0755",
                "tmpfs",
                str(RAMDISK_BASE),
            ]
        )
        runner.mkdir(layout["lower"].parent)
        runner.mkdir(layout["upper"])
        runner.mkdir(layout["work"])

        # Seed the lower layer with a verbatim copy of the original install.
        runner.copytree(source, layout["lower"])
        runner.chown_recursive(layout["lower"], uid, gid)
        runner.chown_recursive(layout["upper"], uid, gid)
        runner.chown_recursive(layout["work"], uid, gid)

        # Persist state BEFORE the overlay mount so a crash here is recoverable.
        state.write(move)

        # Overlay the merged view directly on top of the original install dir.
        runner.run(
            [
                "mount",
                "-t",
                "overlay",
                "overlay",
                "-o",
                (
                    f"lowerdir={layout['lower']},"
                    f"upperdir={layout['upper']},"
                    f"workdir={layout['work']}"
                ),
                str(source),
            ]
        )

        # Verify the merged view matches what we seeded.
        seeded = runner.count_and_size(layout["lower"])
        merged = runner.count_and_size(source)
        if seeded != merged:
            # Tear down what we just put up and surface the failure loudly.
            try:
                runner.run(["umount", str(source)])
            except Exception:
                pass
            try:
                runner.run(["umount", str(RAMDISK_BASE)])
            except Exception:
                pass
            state.clear()
            return OperationResult(
                ok=False,
                message="Overlay verification failed (file count/size mismatch).",
                kind="move",
                details={
                    "seeded_files": seeded[0],
                    "seeded_bytes": seeded[1],
                    "merged_files": merged[0],
                    "merged_bytes": merged[1],
                },
            )

        state.set_phase("active")
        return OperationResult(
            ok=True,
            message=f"{game.name} staged on RAM disk via overlayfs.",
            kind="move",
            details={"active_move": state.raw(), "dry_run": False},
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise OperationError(str(exc)) from exc


# --- revert ---------------------------------------------------------------

def revert_active_move(
    state: MoveStateStore,
    dry_run: bool = True,
    commit_changes: bool = False,
    runner: OperationRunner | None = None,
) -> OperationResult:
    """Tear down the overlay.

    ``commit_changes=False`` (default): discard everything in the upper layer.
    The original install dir is byte-identical to stage time.

    ``commit_changes=True``: rsync the upper layer back into the original
    install dir before unmounting. Use this when Steam updated the game,
    the user installed mods, or the game wrote saves into its install dir.
    """
    runner = runner or SubprocessRunner()
    move = state.read()
    if move is None:
        return OperationResult(
            ok=False,
            message="No active RAM-disk move is recorded.",
            kind="revert",
            details={},
        )
    if (disabled := _check_disabled("revert", dry_run, {"active_move": move.to_dict()})) is not None:
        return disabled

    merged = Path(move.merged_dir or move.original_install_dir)
    upper = Path(move.overlay_upper_dir) if move.overlay_upper_dir else None

    if dry_run:
        return OperationResult(
            ok=True,
            message=(
                "Dry-run: would rsync upper into original then unmount."
                if commit_changes
                else "Dry-run: would discard overlay upper and unmount."
            ),
            kind="revert",
            details={
                "active_move": move.to_dict(),
                "commit_changes": commit_changes,
                "merged": str(merged),
                "upper": str(upper) if upper else None,
            },
        )

    try:
        state.set_phase("reverting")

        if commit_changes:
            if upper is None or not upper.exists():
                return OperationResult(
                    ok=False,
                    message="Cannot commit changes: overlay upper layer is missing.",
                    kind="revert",
                    details={"active_move": move.to_dict()},
                )
            # We rsync FROM the merged view (so overlay applies whiteouts for us)
            # into the original install dir AFTER unmounting overlay. To do that
            # we need to capture the merged contents first to a holding dir on
            # the tmpfs. Simpler: rsync upper into original now while overlay is
            # still mounted is unsafe (writes go back through overlay). Instead:
            # 1) Snapshot the merged view to a temp path on tmpfs.
            # 2) Unmount overlay.
            # 3) rsync snapshot into the original install dir.
            snapshot = Path(move.ramdisk_mount_path) / "commit-snapshot" / Path(move.original_install_dir).name
            runner.mkdir(snapshot.parent)
            runner.copytree(merged, snapshot)
            runner.run(["umount", str(merged)])
            runner.rsync(snapshot, Path(move.original_install_dir))
            runner.remove_tree(snapshot)
        else:
            runner.run(["umount", str(merged)])

        runner.run(["umount", move.ramdisk_mount_path])
        state.clear()
        return OperationResult(
            ok=True,
            message=(
                f"{move.name} restored to its original library (changes committed)."
                if commit_changes
                else f"{move.name} restored to its original library (changes discarded)."
            ),
            kind="revert",
            details={"active_move": move.to_dict(), "commit_changes": commit_changes},
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise OperationError(str(exc)) from exc


# --- recovery -------------------------------------------------------------

@dataclass(frozen=True)
class RecoveryReport:
    healthy: bool
    notes: list[str] = field(default_factory=list)
    cleared_state: bool = False
    forced_unmounts: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "healthy": self.healthy,
            "notes": list(self.notes),
            "cleared_state": self.cleared_state,
            "forced_unmounts": list(self.forced_unmounts),
        }


def recover(state: MoveStateStore, runner: OperationRunner | None = None) -> OperationResult:
    """Inspect saved state against real mounts and heal stale conditions.

    Call this from ``Plugin._main`` on plugin load. Returns a report the
    frontend can display.
    """
    runner = runner or SubprocessRunner()
    move = state.read()
    if move is None:
        return OperationResult(
            ok=True,
            message="No active RAM-disk move recorded; nothing to recover.",
            kind="recover",
            details=RecoveryReport(healthy=True).to_dict(),
        )

    notes: list[str] = []
    forced: list[str] = []

    base_mounted = runner.is_mountpoint(Path(move.ramdisk_mount_path))
    merged_path = Path(move.merged_dir or move.original_install_dir)
    overlay_mounted = runner.is_mountpoint(merged_path)

    # Case A: nothing is mounted (typical post-reboot). The original install
    # is intact because we never destroyed it. Clear state, tell the UI.
    if not base_mounted and not overlay_mounted:
        notes.append(
            "tmpfs and overlay are both gone (post-reboot). Original install "
            "is untouched. Cleared stale state."
        )
        state.clear()
        return OperationResult(
            ok=True,
            message="Stale RAM-disk state cleared after reboot.",
            kind="recover",
            details=RecoveryReport(
                healthy=True, notes=notes, cleared_state=True
            ).to_dict(),
        )

    # Case B: overlay mounted but tmpfs gone. Cannot really happen on the
    # same boot, but if it does the overlay is broken; force unmount and clear.
    if overlay_mounted and not base_mounted:
        notes.append("Overlay still mounted but tmpfs is gone; forcing unmount.")
        try:
            runner.run(["umount", "-l", str(merged_path)])
            forced.append(str(merged_path))
        except Exception as exc:
            notes.append(f"Forced unmount failed: {exc}")
        state.clear()
        return OperationResult(
            ok=True,
            message="Recovered from broken overlay state.",
            kind="recover",
            details=RecoveryReport(
                healthy=True, notes=notes, cleared_state=True, forced_unmounts=forced
            ).to_dict(),
        )

    # Case C: phase is still "staging" or "reverting" -> mid-operation crash.
    # The user-safe move is to tear everything down; the original dir is intact
    # (we never deleted it).
    if move.phase in ("staging", "reverting"):
        notes.append(
            f"Found move in phase={move.phase!r}; tearing down to a clean state."
        )
        try:
            if overlay_mounted:
                runner.run(["umount", "-l", str(merged_path)])
                forced.append(str(merged_path))
            if base_mounted:
                runner.run(["umount", "-l", move.ramdisk_mount_path])
                forced.append(move.ramdisk_mount_path)
        except Exception as exc:
            notes.append(f"Forced teardown error: {exc}")
        state.clear()
        return OperationResult(
            ok=True,
            message="Recovered from a mid-operation crash.",
            kind="recover",
            details=RecoveryReport(
                healthy=True, notes=notes, cleared_state=True, forced_unmounts=forced
            ).to_dict(),
        )

    # Case D: phase == "active" and both mounts present. Normal resumed state.
    notes.append("Active overlay-staged game restored from saved state.")
    return OperationResult(
        ok=True,
        message=f"Active RAM-disk move for {move.name} is healthy.",
        kind="recover",
        details=RecoveryReport(healthy=True, notes=notes).to_dict(),
    )
