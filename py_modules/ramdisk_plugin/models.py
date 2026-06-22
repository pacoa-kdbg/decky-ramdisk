from dataclasses import asdict, dataclass, field
from typing import Any, Literal


# Lifecycle phases for a staged move. Recorded in ActiveMove.phase so a crash
# or reboot can be diagnosed by the recovery pass on startup.
Phase = Literal["staging", "active", "reverting"]


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
    # New fields. Defaulted so legacy state files written by older versions
    # (which had none of these) still load cleanly.
    phase: Phase = "active"
    overlay_lower_dir: str = ""
    overlay_upper_dir: str = ""
    overlay_work_dir: str = ""
    merged_dir: str = ""
    staged_at: float = 0.0
    schema_version: int = 2

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class OperationResult:
    ok: bool
    message: str
    kind: Literal["scan", "plan", "move", "revert", "recover", "error"]
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
