from .models import MemoryInfo


def _parse_kib(line: str) -> int:
    parts = line.split()
    if len(parts) < 2:
        return 0
    return int(parts[1]) * 1024


def read_memory_info(meminfo_path: str = "/proc/meminfo", max_fraction: float = 0.5) -> MemoryInfo:
    values: dict[str, int] = {}
    with open(meminfo_path, "r", encoding="utf-8") as handle:
        for line in handle:
            key = line.split(":", 1)[0]
            if key in {"MemTotal", "MemAvailable"}:
                values[key] = _parse_kib(line)

    total = values.get("MemTotal", 0)
    available = values.get("MemAvailable", 0)
    return MemoryInfo(
        total_bytes=total,
        available_bytes=available,
        max_game_bytes=int(total * max_fraction),
    )

