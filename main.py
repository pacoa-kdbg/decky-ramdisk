import os
import sys
import traceback

import decky

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "py_modules"))

from ramdisk_plugin.memory import read_memory_info
from ramdisk_plugin.operations import OperationError, revert_active_move, stage_game
from ramdisk_plugin.state import MoveStateStore
from ramdisk_plugin.steam import installed_games


def _settings_dir() -> str:
    return os.environ.get("DECKY_PLUGIN_SETTINGS_DIR", os.path.join(os.path.dirname(__file__), ".settings"))


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
            matches = [game for game in installed_games(_user_home(), memory.max_game_bytes) if game.appid == str(appid)]
            if not matches:
                return {
                    "ok": False,
                    "message": "Game was not found or is larger than the RAM-disk size limit.",
                    "kind": "move",
                    "details": {"appid": appid},
                }
            return stage_game(matches[0], MoveStateStore(_settings_dir()), dry_run=dry_run).to_dict()
        except OperationError as exc:
            decky.logger.error(traceback.format_exc())
            return {"ok": False, "message": str(exc), "kind": "error", "details": {}}

    async def revert(self, dry_run: bool = True):
        try:
            return revert_active_move(MoveStateStore(_settings_dir()), dry_run=dry_run).to_dict()
        except OperationError as exc:
            decky.logger.error(traceback.format_exc())
            return {"ok": False, "message": str(exc), "kind": "error", "details": {}}

    async def _main(self):
        decky.logger.info("Decky RAMDisk backend loaded")

    async def _unload(self):
        decky.logger.info("Decky RAMDisk backend unloading")

    async def _uninstall(self):
        decky.logger.info("Decky RAMDisk uninstall requested; active staged games should be reverted first")
