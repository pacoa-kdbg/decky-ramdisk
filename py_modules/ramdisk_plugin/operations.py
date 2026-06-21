import os
import shutil
import subprocess
from pathlib import Path

from .models import ActiveMove, OperationResult, SteamGame
from .state import MoveStateStore


RAMDISK_BASE = Path("/run/media/decky-ramdisk")
RAMDISK_LIBRARY = RAMDISK_BASE / "SteamLibrary"


class OperationError(RuntimeError):
    pass


def mutations_enabled() -> bool:
    return os.environ.get("DECKY_RAMDISK_ENABLE_MUTATIONS") == "1"


def _run(command: list[str], dry_run: bool) -> None:
    if dry_run:
        return
    subprocess.run(command, check=True)


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
        },
    )


def stage_game(game: SteamGame, state: MoveStateStore, dry_run: bool = True) -> OperationResult:
    plan = plan_move(game, state)
    if not plan.ok:
        return plan
    if not dry_run and not mutations_enabled():
        return OperationResult(
            ok=False,
            message="Real moves are disabled until DECKY_RAMDISK_ENABLE_MUTATIONS=1 is set for development testing.",
            kind="move",
            details={"dry_run": dry_run},
        )

    source = Path(game.install_dir)
    target_common = RAMDISK_LIBRARY / "steamapps" / "common"
    target = target_common / source.name
    manifest = Path(game.library_path) / "steamapps" / f"appmanifest_{game.appid}.acf"
    target_manifest = RAMDISK_LIBRARY / "steamapps" / manifest.name

    try:
        _run(["mkdir", "-p", str(target_common)], dry_run)
        _run(["mount", "-t", "tmpfs", "-o", f"size={game.size_on_disk + 1024**3}", "tmpfs", str(RAMDISK_BASE)], dry_run)
        if not dry_run:
            target_common.mkdir(parents=True, exist_ok=True)
            shutil.copytree(source, target, symlinks=True)
            shutil.copy2(manifest, target_manifest)
            shutil.rmtree(source)
            os.symlink(target, source)
        move = ActiveMove(
            appid=game.appid,
            name=game.name,
            original_library_path=game.library_path,
            original_install_dir=game.install_dir,
            ramdisk_library_path=str(RAMDISK_LIBRARY),
            ramdisk_mount_path=str(RAMDISK_BASE),
            size_on_disk=game.size_on_disk,
        )
        if not dry_run:
            state.write(move)
        return OperationResult(
            ok=True,
            message="Dry-run move plan created." if dry_run else f"{game.name} staged on RAM disk.",
            kind="move",
            details={"active_move": move.to_dict(), "dry_run": dry_run},
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise OperationError(str(exc)) from exc


def revert_active_move(state: MoveStateStore, dry_run: bool = True) -> OperationResult:
    move = state.read()
    if move is None:
        return OperationResult(ok=False, message="No active RAM-disk move is recorded.", kind="revert", details={})
    if not dry_run and not mutations_enabled():
        return OperationResult(
            ok=False,
            message="Real restores are disabled until DECKY_RAMDISK_ENABLE_MUTATIONS=1 is set for development testing.",
            kind="revert",
            details={"dry_run": dry_run, "active_move": move.to_dict()},
        )

    source = Path(move.original_install_dir)
    ram_source = Path(move.ramdisk_mount_path) / "SteamLibrary" / "steamapps" / "common" / source.name
    try:
        if not dry_run:
            if source.is_symlink():
                source.unlink()
            shutil.copytree(ram_source, source, symlinks=True)
            _run(["umount", move.ramdisk_mount_path], dry_run=False)
            state.clear()
        return OperationResult(
            ok=True,
            message="Dry-run revert plan created." if dry_run else f"{move.name} restored to its original library.",
            kind="revert",
            details={"active_move": move.to_dict(), "dry_run": dry_run},
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise OperationError(str(exc)) from exc
