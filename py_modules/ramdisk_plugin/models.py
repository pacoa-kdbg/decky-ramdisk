from dataclasses import asdict, dataclass
from typing import Any, Literal


@dataclass(frozen=True)
class MemoryInfo:
    total_bytes: int
    available_bytes: int
    max_game_bytes: int

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


@dataclass(frozen=True)
class SteamGame:
    appid: str
    name: str
    install_dir: str
    library_path: str
    size_on_disk: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ActiveMove:
    appid: str
    name: str
    original_library_path: str
    original_install_dir: str
    ramdisk_library_path: str
    ramdisk_mount_path: str
    size_on_disk: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class OperationResult:
    ok: bool
    message: str
    kind: Literal["scan", "plan", "move", "revert", "error"]
    details: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

