import os
from pathlib import Path
from typing import Any

from .models import SteamGame
from .vdf import loads


STEAM_ROOT_CANDIDATES = (
    ".steam/steam",
    ".local/share/Steam",
)

EXCLUDED_APP_IDS = {
    "228980",  # Steamworks Common Redistributables
    "1391110",  # Steam Linux Runtime - Soldier
    "1628350",  # Steam Linux Runtime - Sniper
    "1887720",  # Proton Experimental
    "2180100",  # Proton Hotfix
}

EXCLUDED_NAME_PREFIXES = (
    "Proton ",
    "Steam Linux Runtime",
    "Steamworks Common Redistributables",
)


def _read_vdf(path: Path) -> dict[str, Any]:
    return loads(path.read_text(encoding="utf-8", errors="replace"))


def find_steam_root(home: str) -> Path | None:
    for relative in STEAM_ROOT_CANDIDATES:
        candidate = Path(home) / relative
        if (candidate / "steamapps").exists():
            return candidate
    return None


def find_library_paths(home: str) -> list[Path]:
    root = find_steam_root(home)
    if root is None:
        return []

    paths = [root]
    library_vdf = root / "steamapps" / "libraryfolders.vdf"
    if not library_vdf.exists():
        return paths

    data = _read_vdf(library_vdf).get("libraryfolders", {})
    for value in data.values():
        if isinstance(value, dict) and "path" in value:
            paths.append(Path(str(value["path"])))

    unique: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        resolved = str(path.expanduser().resolve())
        if resolved not in seen:
            unique.append(path.expanduser())
            seen.add(resolved)
    return unique


def _is_excluded_tool(appid: str, name: str) -> bool:
    if appid in EXCLUDED_APP_IDS:
        return True
    return any(name.startswith(prefix) for prefix in EXCLUDED_NAME_PREFIXES)


def _manifest_size(manifest: dict[str, Any], install_path: Path) -> int:
    try:
        return int(manifest.get("SizeOnDisk", 0))
    except (TypeError, ValueError):
        return directory_size(install_path)


def directory_size(path: Path) -> int:
    total = 0
    if not path.exists():
        return total
    for root, _, files in os.walk(path):
        for file_name in files:
            file_path = Path(root) / file_name
            try:
                total += file_path.stat().st_size
            except OSError:
                continue
    return total


def installed_games(home: str, max_size_bytes: int | None = None) -> list[SteamGame]:
    games_by_appid: dict[str, SteamGame] = {}
    for library in find_library_paths(home):
        steamapps = library / "steamapps"
        common = steamapps / "common"
        if not steamapps.exists():
            continue
        for manifest_path in steamapps.glob("appmanifest_*.acf"):
            manifest = _read_vdf(manifest_path).get("AppState", {})
            appid = str(manifest.get("appid", manifest_path.stem.removeprefix("appmanifest_")))
            name = str(manifest.get("name", f"App {appid}"))
            if _is_excluded_tool(appid, name):
                continue
            install_dir_name = str(manifest.get("installdir", ""))
            install_dir = common / install_dir_name
            size = _manifest_size(manifest, install_dir)
            if max_size_bytes is not None and size > max_size_bytes:
                continue
            game = SteamGame(
                appid=appid,
                name=name,
                install_dir=str(install_dir),
                library_path=str(library),
                size_on_disk=size,
            )
            existing = games_by_appid.get(appid)
            if existing is None or game.size_on_disk > existing.size_on_disk:
                games_by_appid[appid] = game
    return sorted(games_by_appid.values(), key=lambda game: game.name.casefold())
