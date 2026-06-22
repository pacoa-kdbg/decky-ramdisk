import os
import sys
import traceback

import decky

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "py_modules"))

from ramdisk_plugin.memory import read_memory_info
from ramdisk_plugin.operations import (
    OperationError,
    recover as recover_state,
    revert_active_move,
    stage_game,
)
from ramdisk_plugin.state import MoveStateStore
from ramdisk_plugin.steam import installed_games


def _settings_dir() -> str:
    return os.environ.get(
        "DECKY_PLUGIN_SETTINGS_DIR",
        os.path.join(os.path.dirname(__file__), ".settings"),
    )


def _user_home() -> str:
    return os.environ.get("DECKY_USER_HOME", os.path.expanduser("~"))


class Plugin:
    async def get_status(self):
        memory = read_memory_info()
        state = MoveStateStore(_settings_dir())
        return {
            "memory": memory.to_dict(),
            "active_move": state.raw(),
        }

    async def list_games(self):
        memory = read_memory_info()
        games = installed_games(_user_home(), max_size_bytes=memory.max_game_bytes)
        return {
            "memory": memory.to_dict(),
            "games": [game.to_dict() for game in games],
        }

    async def stage_game(self, appid: str, dry_run: bool = True):
        try:
            memory = read_memory_info()
            matches = [
                game
                for game in installed_games(_user_home(), memory.max_game_bytes)
                if game.appid == str(appid)
            ]
            if not matches:
                decky.logger.warning(
                    f"Stage request rejected: appid={appid} dry_run={dry_run} was not eligible"
                )
                return {
                    "ok": False,
                    "message": "Game was not found or is larger than the RAM-disk size limit.",
                    "kind": "move",
                    "details": {"appid": appid},
                }
            game = matches[0]
            decky.logger.info(
                f"Stage request: appid={game.appid} name={game.name!r} size={game.size_on_disk} dry_run={dry_run}"
            )
            result = stage_game(game, MoveStateStore(_settings_dir()), dry_run=dry_run)
            decky.logger.info(
                f"Stage result: ok={result.ok} kind={result.kind} message={result.message!r}"
            )
            return result.to_dict()
        except OperationError as exc:
            decky.logger.error(traceback.format_exc())
            return {"ok": False, "message": str(exc), "kind": "error", "details": {}}

    async def revert(self, dry_run: bool = True, commit_changes: bool = False):
        try:
            decky.logger.info(
                f"Revert request: dry_run={dry_run} commit_changes={commit_changes}"
            )
            result = revert_active_move(
                MoveStateStore(_settings_dir()),
                dry_run=dry_run,
                commit_changes=commit_changes,
            )
            decky.logger.info(
                f"Revert result: ok={result.ok} kind={result.kind} message={result.message!r}"
            )
            return result.to_dict()
        except OperationError as exc:
            decky.logger.error(traceback.format_exc())
            return {"ok": False, "message": str(exc), "kind": "error", "details": {}}

    async def recover(self):
        """Inspect and heal stale RAM-disk state. Safe to call any time."""
        try:
            result = recover_state(MoveStateStore(_settings_dir()))
            decky.logger.info(
                f"Recover result: ok={result.ok} kind={result.kind} message={result.message!r}"
            )
            return result.to_dict()
        except OperationError as exc:
            decky.logger.error(traceback.format_exc())
            return {"ok": False, "message": str(exc), "kind": "error", "details": {}}

    async def _main(self):
        decky.logger.info("Decky RAMDisk backend loaded")
        # Heal stale state from a previous boot / crashed operation before
        # exposing any other endpoints. Failures here are logged, never raised.
        try:
            report = recover_state(MoveStateStore(_settings_dir()))
            decky.logger.info(f"Startup recovery: {report.message}")
        except Exception:
            decky.logger.error(
                "Startup recovery failed:\n" + traceback.format_exc()
            )

    async def _unload(self):
        decky.logger.info("Decky RAMDisk backend unloading")

    async def _uninstall(self):
        # Refuse uninstall while a move is recorded; the user should revert
        # first so we don't leave an overlay mounted with no UI to control it.
        state = MoveStateStore(_settings_dir())
        active = state.raw()
        if active is not None:
            decky.logger.warning(
                "Uninstall requested while a RAM-disk move is active; "
                "user should revert from the plugin UI first. State: "
                f"{active}"
            )
